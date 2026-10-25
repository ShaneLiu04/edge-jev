"""请求级代价预算（AR002 FR2-2，FrugalGPT 式级联预算的确定性实现）。

预算只约束「是否值得升级到 full」；不改变已有路由语义：
- max_brain_tokens 为 None → 不限
- 估算 brain tokens ≈ ceil(state_tokens × 1.1) + LIGHT 预算
- 预估超预算 → allows_full() 为 False（路由侧降级 light 并截断）
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_COMPLETION_TOKENS = 512
OVERHEAD_NUM, OVERHEAD_DEN = 11, 10


@dataclass(frozen=True)
class BudgetContext:
    """单请求预算上下文（None 表示该维度不限）。"""

    max_brain_tokens: int | None = None
    max_latency_ms: float | None = None
    state_tokens: int = 0
    completion_tokens: int = DEFAULT_COMPLETION_TOKENS

    def estimated_brain_tokens(self) -> int:
        # 整数上限（避免浮点 1.1× 的进位误差）：ceil(1.1·n) = (11n+9)//10
        prompt = (OVERHEAD_NUM * max(self.state_tokens, 0) + OVERHEAD_DEN - 1) // OVERHEAD_DEN
        return prompt + max(self.completion_tokens, 0)

    def allows_full(self) -> bool:
        if self.max_brain_tokens is None:
            return True
        return self.estimated_brain_tokens() <= self.max_brain_tokens

    def effective_max_tokens(self, requested: int) -> int:
        """预算内可用的完成 token（无 token 预算时原样返回）。"""
        if self.max_brain_tokens is None:
            return requested
        prompt = (OVERHEAD_NUM * max(self.state_tokens, 0) + OVERHEAD_DEN - 1) // OVERHEAD_DEN
        available = self.max_brain_tokens - prompt
        if available <= 0:
            return 0
        return min(requested, available)


def budget_from_config(config: dict, *, state_tokens: int = 0) -> BudgetContext | None:
    """从 [cascade] 配置构建预算；未配置或全空 → None（不限）。"""
    max_tokens = config.get("max_brain_tokens")
    max_latency = config.get("max_latency_ms")
    if max_tokens is None and max_latency is None:
        return None
    return BudgetContext(
        max_brain_tokens=int(max_tokens) if max_tokens is not None else None,
        max_latency_ms=float(max_latency) if max_latency is not None else None,
        state_tokens=state_tokens,
    )


__all__ = ["BudgetContext", "budget_from_config"]
