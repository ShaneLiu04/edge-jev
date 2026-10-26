"""联合上下文预算分配（AR005 FR5-3，design §4.2.2）。

把记忆摘要/草稿/prefill 选块统一为带收益与 token 成本的单元，
在单一预算下做确定性 0/1 背包，避免组件各自超支；无法获益时回退原输入。
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Sequence


@dataclasses.dataclass(frozen=True)
class AllocatorConfig:
    enabled: bool = False
    budget_ratio: float = 0.6
    min_prompt_tokens: int = 512
    min_blocks: int = 2
    draft_benefit: float = 0.4
    memory_benefit: float = 0.5

    def __post_init__(self) -> None:
        if not 0.0 < self.budget_ratio <= 1.0:
            raise ValueError(f"budget_ratio 须在 (0,1]: {self.budget_ratio}")
        if self.min_prompt_tokens < 0:
            raise ValueError(f"min_prompt_tokens 不得为负: {self.min_prompt_tokens}")
        if self.min_blocks < 1:
            raise ValueError(f"min_blocks 须 ≥1: {self.min_blocks}")
        if not 0.0 <= self.draft_benefit <= 1.0:
            raise ValueError(f"draft_benefit 须在 [0,1]: {self.draft_benefit}")
        if not 0.0 <= self.memory_benefit <= 1.0:
            raise ValueError(f"memory_benefit 须在 [0,1]: {self.memory_benefit}")


@dataclasses.dataclass(frozen=True)
class Unit:
    key: str
    tokens: int
    benefit: float
    order: int
    mandatory: bool = False
    payload: object = None

    def __post_init__(self) -> None:
        if self.tokens < 0:
            raise ValueError(f"tokens 不得为负: {self.tokens}")
        if not math.isfinite(self.benefit) and not self.mandatory:
            raise ValueError(f"benefit 非法: {self.benefit!r}")


@dataclasses.dataclass(frozen=True)
class Allocation:
    selected: tuple[str, ...]
    omitted: tuple[str, ...]
    before_tokens: int
    after_tokens: int
    skipped: str | None = None

    @property
    def saved_tokens(self) -> int:
        return max(0, self.before_tokens - self.after_tokens)

    def stats(self) -> dict:
        return {
            "selected": len(self.selected),
            "omitted": len(self.omitted),
            "before_tokens": self.before_tokens,
            "after_tokens": self.after_tokens,
            "saved_tokens": self.saved_tokens,
            "skipped": self.skipped,
        }


def _by_order(units: Sequence[Unit]) -> list[Unit]:
    return sorted(units, key=lambda unit: unit.order)


def allocate(
    units: Sequence[Unit],
    *,
    config: AllocatorConfig | None = None,
) -> Allocation:
    """确定性预算分配；输出按 order 排序的 key 元组。"""
    config = config if config is not None else AllocatorConfig()
    items = _by_order(units)
    before = sum(unit.tokens for unit in items)
    all_keys = tuple(unit.key for unit in items)
    if not config.enabled:
        return Allocation(all_keys, (), before, before, "disabled")
    if before < config.min_prompt_tokens:
        return Allocation(all_keys, (), before, before, "short")

    mandatory = [unit for unit in items if unit.mandatory]
    optional = [unit for unit in items if not unit.mandatory]
    if len(items) - len(mandatory) < config.min_blocks:
        return Allocation(all_keys, (), before, before, "few_units")

    budget = max(1, math.ceil(before * config.budget_ratio))
    mandatory_tokens = sum(unit.tokens for unit in mandatory)
    if mandatory_tokens > budget:
        return Allocation(all_keys, (), before, before, "mandatory_over_budget")

    capacity = budget - mandatory_tokens
    values = [0.0] * (capacity + 1)
    chosen = [False] * len(optional)
    keep = [[False] * (capacity + 1) for _ in optional]
    for index, unit in enumerate(optional):
        cost = unit.tokens
        benefit = unit.benefit
        for room in range(capacity, cost - 1, -1):
            candidate = values[room - cost] + benefit
            if candidate >= values[room]:
                values[room] = candidate
                keep[index][room] = True
    room = capacity
    for index in range(len(optional) - 1, -1, -1):
        if keep[index][room]:
            chosen[index] = True
            room -= optional[index].tokens

    selected_units = mandatory + [
        unit for unit, picked in zip(optional, chosen, strict=True) if picked
    ]
    selected_keys = tuple(unit.key for unit in _by_order(selected_units))
    after = sum(unit.tokens for unit in selected_units)
    if after >= before:
        return Allocation(all_keys, (), before, before, "no_gain")
    omitted = tuple(key for key in all_keys if key not in set(selected_keys))
    return Allocation(selected_keys, omitted, before, after, None)


__all__ = ["Allocation", "AllocatorConfig", "Unit", "allocate"]
