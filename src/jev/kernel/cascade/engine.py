"""置信门控级联引擎（design.md §4.2 流程图 A，srs FR5）。

三路径判定（direct 直答 / light 草拟+校验 / full 直大脑）、MID 早停、
POST 采纳/重试/降级——全部基于决策批的 applied 值（tau 门控后语义值）。
只认 ACTIVE 决策；SHADOW 只记录不参与控制流；PRE 批失败（from_fallback）
安全侧默认 full。posterior 产出后验回流信号（供台账 T012/蒸馏采集 T018）。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from jev.errors import JevProtocolError
from jev.kernel.decisions.framework import Decision, DecisionRegistry, Mode


@dataclass(frozen=True)
class RoutePolicy:
    """路由策略开关（AR002 能力门控；关闭即恢复 AR001 行为）。

    capability_gate=True 时：
    - direct 额外接受「无需生成文本 + 无需推理 + 低复杂度」正交信号
      （真实 laya 的 intent 探针区分度弱，单靠 intent 命中率为 0%）；
    - light 仅要求「无需推理 + 安全」，不再强制 complexity==low
      （真实 laya 的 score 型复杂度置信度不稳定）。
    两者均以真实资产实测（20 题：命中率 10% → 75%）为依据。
    """

    capability_gate: bool = True
    light_requires_low_complexity: bool = False


class Route(Enum):
    """级联三路径（X-Jev-Route 头取 wire 小写值）。"""

    DIRECT = "direct"
    LIGHT = "light"
    FULL = "full"

    @property
    def wire(self) -> str:
        return self.value


class MidAction(Enum):
    """MID 分段生成动作：早停/继续。"""

    CONTINUE = "continue"
    STOP = "stop"


class PostVerdict(Enum):
    """POST 复核裁决：采纳/重试一次/降级。"""

    ACCEPT = "accept"
    RETRY = "retry"
    DOWNGRADE = "downgrade"


class CascadeEngine:
    """三路径判定与分段控制流（纯逻辑，无后端调用）。"""

    def __init__(self, registry: DecisionRegistry, *, policy: RoutePolicy | None = None) -> None:
        self._registry = registry
        self._policy = policy if policy is not None else RoutePolicy()

    def route_chat(
        self,
        pre: Sequence[Decision],
        *,
        margin_tau: float = 0.0,
        budget=None,
    ) -> Route:
        """三路径判定；AR002 扩展 margin 升级、能力门控与预算约束。

        margin_tau > 0 时，direct/light 分支额外要求 margin ≥ 阈值
        （AnyJev 式：高置信但 top1-top2 差距小 → 不可靠 → 升级 full）。
        budget（BudgetContext）超限时收敛到 light 并标记。
        """
        active = [d for d in pre if d.mode is Mode.ACTIVE]
        # 小脑批量失败 → 安全侧默认完整生成
        if any(d.from_fallback for d in active):
            return Route.FULL
        if not self._margin_ok(pre, margin_tau):
            return Route.FULL
        # 显式不安全即不降级（安全点缺失时沿用原有行为，不阻断最小装配）
        if self._applied(pre, "safety") == "unsafe":
            return Route.FULL
        # 路径①：结构化决策类意图高置信 → 小脑直答
        if self._applied(pre, "intent") == "decision":
            return Route.DIRECT
        if (
            self._policy.capability_gate
            and self._applied(pre, "needs_generation") == "no_generation"
            and self._applied(pre, "needs_reasoning") == "no_reasoning"
            and self._applied(pre, "complexity") == "low"
        ):
            return Route.DIRECT
        # 路径②：无需推理 → 轻量生成（capability_gate 下放宽复杂度门）
        reasoning = self._applied(pre, "needs_reasoning")
        if self._policy.capability_gate:
            light_ok = reasoning == "no_reasoning"
        else:
            light_ok = reasoning == "no_reasoning" and self._applied(pre, "complexity") == "low"
        if self._policy.capability_gate and self._policy.light_requires_low_complexity:
            light_ok = light_ok and self._applied(pre, "complexity") == "low"
        if light_ok:
            return Route.LIGHT
        # 路径③：完整生成（预算不足时降级 light，由 chat 侧截断 max_tokens）
        if budget is not None and hasattr(budget, "allows_full") and not budget.allows_full():
            return Route.LIGHT
        return Route.FULL

    def handle_mid(self, mid: Sequence[Decision]) -> MidAction:
        if self._applied(mid, "should_stop") == "stop":
            return MidAction.STOP
        if self._applied(mid, "on_track") == "off_track":
            return MidAction.STOP
        return MidAction.CONTINUE

    def handle_post(
        self,
        answer: str,
        post: Sequence[Decision],
        *,
        retried: bool = False,
    ) -> PostVerdict:
        if not retried and self._applied(post, "needs_retry") == "retry":
            return PostVerdict.RETRY
        if retried and (
            self._applied(post, "answer_complete") == "incomplete"
            or self._applied(post, "matches_request") == "off_topic"
        ):
            return PostVerdict.DOWNGRADE
        return PostVerdict.ACCEPT

    def posterior(
        self, route: Route, decisions: Sequence[Decision]
    ) -> dict:
        """后验回流信号：路由去向 + 每点决策（含 applied 门控值），供蒸馏采集。"""
        entries: list[dict] = []
        for decision in decisions:
            try:
                point = self._registry.get(decision.point_id)
            except JevProtocolError:
                # 注册表无该点（如已注销的自定义点）——信号中跳过
                continue
            entries.append(
                {
                    "point_id": decision.point_id,
                    "value": decision.value,
                    "confidence": decision.confidence,
                    "applied": decision.applied_value(point),
                    "from_fallback": decision.from_fallback,
                    "mode": decision.mode.value,
                    "margin": decision.margin,
                    "tau_used": decision.tau if decision.tau is not None else point.tau,
                }
            )
        return {"route": route.wire, "decisions": entries}

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _margin_ok(self, decisions: Sequence[Decision], margin_tau: float) -> bool:
        """margin 门：threshold≤0 关闭；无 margin（单探针未融合）视为通过。"""
        if margin_tau <= 0:
            return True
        for decision in decisions:
            if decision.mode is not Mode.ACTIVE or decision.margin is None:
                continue
            if decision.margin < margin_tau:
                return False
        return True

    def _applied(self, decisions: Sequence[Decision], point_id: str) -> str | float | None:
        """取指定点 ACTIVE 决策的 applied 值；缺失/非 ACTIVE/注册表无该点 → None。

        注册表缺失（纯逻辑测试/最小装配）时退回原始 value（不做 tau 门控）。
        """
        for decision in decisions:
            if decision.point_id != point_id or decision.mode is not Mode.ACTIVE:
                continue
            if self._registry is None:
                return decision.value
            try:
                point = self._registry.get(point_id)
            except JevProtocolError:
                return None
            return decision.applied_value(point)
        return None
