"""预填预算规划（AR003 FR3-1/FR3-2，design §4.2.2(7)）。

确定性选择：首末块必选；其余按显著性降序填满预算后按原顺序重建 messages。
跳过条件：功能关闭 / prompt 过短 / 块数不足 / 强制块超预算。
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping, Sequence

from jev.kernel.prefill.blocks import Block, estimate_tokens, split_blocks
from jev.kernel.prefill.salience import score_blocks


@dataclasses.dataclass(frozen=True)
class PrefillConfig:
    enabled: bool = False
    budget_ratio: float = 0.6
    min_prompt_tokens: int = 512
    min_blocks: int = 4
    block_tokens: int = 256

    def __post_init__(self) -> None:
        if not 0.0 < self.budget_ratio <= 1.0:
            raise ValueError(f"budget_ratio 须在 (0,1]: {self.budget_ratio}")
        if self.min_prompt_tokens < 0:
            raise ValueError(f"min_prompt_tokens 不得为负: {self.min_prompt_tokens}")
        if self.min_blocks < 2:
            raise ValueError(f"min_blocks 须 ≥2: {self.min_blocks}")
        if self.block_tokens < 32:
            raise ValueError(f"block_tokens 须 ≥32: {self.block_tokens}")


@dataclasses.dataclass(frozen=True)
class PrefillPlan:
    messages: list[dict]
    selected: int
    total: int
    before_tokens: int
    after_tokens: int
    skipped: str | None = None


def _rebuild(messages: Sequence[dict], blocks: Sequence[Block], chosen: set[int]) -> list[dict]:
    """按原消息/块顺序重建；同消息内选中块以空行连接。"""
    rebuilt: list[dict] = []
    for message_index, message in enumerate(messages):
        parts = [
            block.text
            for block in blocks
            if block.message_index == message_index and block.index in chosen
        ]
        if not parts:
            continue
        rebuilt.append(
            {"role": message.get("role", ""), "content": "\n\n".join(parts)}
        )
    return rebuilt


def plan_prefill(
    messages: Sequence[dict],
    query: str,
    applied: Mapping[str, str | float | None],
    config: PrefillConfig | None = None,
) -> PrefillPlan:
    config = config if config is not None else PrefillConfig()
    original = [dict(message) for message in messages]
    before_tokens = sum(
        estimate_tokens(str(message.get("content", ""))) for message in original
    )
    if not config.enabled:
        return PrefillPlan(original, 0, 0, before_tokens, before_tokens, "disabled")

    blocks = split_blocks(original, block_tokens=config.block_tokens)
    total = len(blocks)
    if before_tokens < config.min_prompt_tokens:
        return PrefillPlan(original, total, total, before_tokens, before_tokens, "short")
    if total < config.min_blocks:
        return PrefillPlan(
            original, total, total, before_tokens, before_tokens, "few_blocks"
        )

    scores = score_blocks(blocks, query, applied)
    budget = max(1, math.ceil(before_tokens * config.budget_ratio))
    mandatory = {0, total - 1}
    mandatory_tokens = sum(blocks[index].tokens for index in mandatory)
    if mandatory_tokens > budget:
        return PrefillPlan(
            original, total, total, before_tokens, before_tokens, "mandatory_over_budget"
        )

    chosen = set(mandatory)
    used = mandatory_tokens
    order = sorted(range(total), key=lambda index: (-scores[index], index))
    for index in order:
        if index in chosen:
            continue
        if used + blocks[index].tokens > budget:
            continue
        chosen.add(index)
        used += blocks[index].tokens

    rebuilt = _rebuild(original, blocks, chosen)
    after_tokens = sum(
        estimate_tokens(str(message.get("content", ""))) for message in rebuilt
    )
    return PrefillPlan(
        messages=rebuilt,
        selected=len(chosen),
        total=total,
        before_tokens=before_tokens,
        after_tokens=after_tokens,
        skipped=None,
    )


__all__ = ["PrefillConfig", "PrefillPlan", "plan_prefill", "split_blocks"]
