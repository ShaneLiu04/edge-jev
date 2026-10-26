"""预填预算规划（AR003 FR3-1/FR3-2，AR006 FR6-1/FR6-2）。

确定性选择：首末块必选；AR006 证据模式额外强制证据锚点，并用 MMR 抑制冗余。
预算比例可固定（0.6）或按分数熵/top-gap 自适应；跳过条件保持不变。
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping, Sequence

from jev.kernel.prefill.blocks import Block, estimate_tokens, split_blocks
from jev.kernel.prefill.evidence import (
    EvidenceConfig,
    EvidenceSelector,
    adaptive_ratio,
    mmr_order,
)
from jev.kernel.prefill.salience import score_blocks


@dataclasses.dataclass(frozen=True)
class PrefillConfig:
    enabled: bool = False
    budget_ratio: float = 0.6
    min_prompt_tokens: int = 512
    min_blocks: int = 4
    block_tokens: int = 256
    adaptive: bool = False
    evidence: bool = False
    anchor: bool = True
    evidence_config: EvidenceConfig = dataclasses.field(default_factory=EvidenceConfig)

    def __post_init__(self) -> None:
        if not 0.0 < self.budget_ratio <= 1.0:
            raise ValueError(f"budget_ratio 须在 (0,1]: {self.budget_ratio}")
        if self.min_prompt_tokens < 0:
            raise ValueError(f"min_prompt_tokens 不得为负: {self.min_prompt_tokens}")
        if self.min_blocks < 2:
            raise ValueError(f"min_blocks 须 >=2: {self.min_blocks}")
        if self.block_tokens < 32:
            raise ValueError(f"block_tokens 须 >=32: {self.block_tokens}")


@dataclasses.dataclass(frozen=True)
class PrefillPlan:
    messages: list[dict]
    selected: int
    total: int
    before_tokens: int
    after_tokens: int
    skipped: str | None = None
    selector: str = "salience"
    budget_ratio: float = 0.0
    entropy: float = 0.0
    top_gap: float = 0.0
    fallback: str | None = None


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
    *,
    evidence: EvidenceSelector | None = None,
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
    vectors: Sequence[Sequence[float]] | None = None
    selector = "salience"
    fallback: str | None = None
    entropy = 0.0
    top_gap = 0.0
    ratio = config.budget_ratio
    if config.evidence:
        if evidence is None:
            fallback = "selector_unavailable"
        else:
            result = evidence.score(blocks, query, applied)
            if result.usable:
                scores = list(result.scores)
                vectors = result.vectors
                selector = "evidence"
                entropy = result.entropy
                top_gap = result.top_gap
                if config.adaptive:
                    engine_config = (
                        evidence.config
                        if evidence is not None
                        else config.evidence_config
                    )
                    ratio = adaptive_ratio(result, engine_config)
            else:
                fallback = result.fallback or "unusable"

    budget = max(1, math.ceil(before_tokens * ratio))
    mandatory = {0, total - 1}
    if config.anchor and config.evidence and selector == "evidence":
        candidates = range(1, total - 1) if total > 2 else range(total)
        anchor = max(candidates, key=lambda index: (scores[index], -index))
        mandatory.add(anchor)
    mandatory_tokens = sum(blocks[index].tokens for index in mandatory)
    if mandatory_tokens > budget:
        return PrefillPlan(
            original,
            total,
            total,
            before_tokens,
            before_tokens,
            "mandatory_over_budget",
            selector,
            ratio,
            entropy,
            top_gap,
            fallback,
        )

    chosen = set(mandatory)
    used = mandatory_tokens
    if vectors is not None:
        order = mmr_order(
            scores,
            vectors,
            mmr_lambda=config.evidence_config.mmr_lambda,
            already=sorted(mandatory),
        )
    else:
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
        selector=selector,
        budget_ratio=ratio,
        entropy=entropy,
        top_gap=top_gap,
        fallback=fallback,
    )


__all__ = ["PrefillConfig", "PrefillPlan", "plan_prefill", "split_blocks"]
