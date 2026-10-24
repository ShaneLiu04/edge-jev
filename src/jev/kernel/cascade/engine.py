"""置信门控级联引擎（design.md §4.2 流程图 A，srs FR5）。

三路径判定（direct 直答 / light 草拟+校验 / full 直大脑）、MID 早停、
POST 采纳/重试/降级——全部基于决策批的 applied 值（tau 门控后语义值）。
只认 ACTIVE 决策；SHADOW 只记录不参与控制流；PRE 批失败（from_fallback）
安全侧默认 full。posterior 产出后验回流信号（供台账 T012/蒸馏采集 T018）。
"""

from collections.abc import Sequence
from enum import Enum

from jev.errors import JevProtocolError
from jev.kernel.decisions.framework import Decision, DecisionRegistry, Mode


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

    def __init__(self, registry: DecisionRegistry) -> None:
        self._registry = registry

    def route_chat(self, pre: Sequence[Decision]) -> Route:
        active = [d for d in pre if d.mode is Mode.ACTIVE]
        # 小脑批量失败 → 安全侧默认完整生成
        if any(d.from_fallback for d in active):
            return Route.FULL
        # 路径①：结构化决策类意图高置信 → 小脑直答
        if self._applied(pre, "intent") == "decision":
            return Route.DIRECT
        # 路径②：无需推理 + 低复杂度 + 安全 → 轻量生成
        if (
            self._applied(pre, "needs_reasoning") == "no_reasoning"
            and self._applied(pre, "complexity") == "low"
            and self._applied(pre, "safety") == "safe"
        ):
            return Route.LIGHT
        # 路径③：完整生成
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
                }
            )
        return {"route": route.wire, "decisions": entries}

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _applied(self, decisions: Sequence[Decision], point_id: str) -> str | float | None:
        """取指定点 ACTIVE 决策的 applied 值；缺失/非 ACTIVE/注册表无该点 → None。"""
        for decision in decisions:
            if decision.point_id != point_id or decision.mode is not Mode.ACTIVE:
                continue
            try:
                point = self._registry.get(point_id)
            except JevProtocolError:
                return None
            return decision.applied_value(point)
        return None
