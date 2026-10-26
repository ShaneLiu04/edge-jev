"""Prompt 编排（AR003 FR2-3/FR3-2，design §4.2.2(5)）。

固定顺序：长程记忆 → 草稿前缀（AR002）→ 预填压缩。
单一入口避免 draft/memory/prefill 三重截断互相覆盖；各步 before/after
token 估计写入台账，最终以响应 usage.prompt_tokens 为准回填。
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence

from jev.kernel.cache.memory import compress_with_memory, memory_tokens
from jev.kernel.cascade.draft import build_draft_prefix
from jev.kernel.decisions.framework import Decision
from jev.kernel.prefill.blocks import estimate_tokens, split_blocks
from jev.kernel.prefill.evidence import (
    EvidenceConfig,
    EvidenceSelector,
    adaptive_ratio,
)
from jev.kernel.prefill.planner import PrefillConfig, PrefillPlan, plan_prefill
from jev.kernel.prefill.salience import score_blocks
from jev.kernel.runtime.allocator import Allocation, AllocatorConfig, Unit, allocate


@dataclasses.dataclass(frozen=True)
class PromptConfig:
    draft_enabled: bool = False
    memory_enabled: bool = False
    prefill_enabled: bool = False
    keep_turns: int = 2
    allocator_enabled: bool = False
    allocator_budget_ratio: float = 0.6
    allocator_min_prompt_tokens: int = 512
    allocator_draft_benefit: float = 0.4
    allocator_memory_benefit: float = 0.5
    evidence_enabled: bool = False
    adaptive: bool = False
    anchor: bool = True
    evidence_config: EvidenceConfig = dataclasses.field(default_factory=EvidenceConfig)

    def __post_init__(self) -> None:
        if self.keep_turns < 1:
            raise ValueError(f"keep_turns 必须 >=1: {self.keep_turns}")
        if not 0.0 < self.allocator_budget_ratio <= 1.0:
            raise ValueError(
                f"allocator_budget_ratio 须在 (0,1]: {self.allocator_budget_ratio}"
            )


@dataclasses.dataclass(frozen=True)
class PromptBuild:
    messages: list[dict]
    before_tokens: int
    after_tokens: int
    memory_summary_tokens: int
    prefill: PrefillPlan | None
    draft_prefix: str
    allocation: Allocation | None = None


def _merge_prefix(messages: Sequence[dict], prefix: str) -> list[dict]:
    if not prefix:
        return [dict(message) for message in messages]
    return [{"role": "system", "content": prefix}, *[dict(m) for m in messages]]


def _is_memory(message: dict) -> bool:
    return message.get("role") == "system" and str(
        message.get("content", "")
    ).startswith("历史摘要：")


def _allocate_prompt(
    working: list[dict],
    draft_prefix: str,
    *,
    query: str,
    applied: Mapping[str, str | float | None],
    config: PromptConfig,
    prefill_config: PrefillConfig,
    evidence: EvidenceSelector | None = None,
) -> tuple[list[dict], Allocation]:
    """JCBA：草稿/记忆摘要/prefill 块在同一预算内竞争；AR006 接入证据与自适应预算。"""
    summary = [m for m in working if _is_memory(m)]
    body = [m for m in working if not _is_memory(m)]
    blocks = split_blocks(body, block_tokens=prefill_config.block_tokens)
    scores = score_blocks(blocks, query, applied)
    selector = "salience"
    fallback: str | None = None
    entropy = 0.0
    top_gap = 0.0
    ratio = config.allocator_budget_ratio
    if config.evidence_enabled and evidence is not None and blocks:
        result = evidence.score(blocks, query, applied)
        if result.usable:
            scores = list(result.scores)
            selector = "evidence"
            entropy = result.entropy
            top_gap = result.top_gap
            if config.adaptive:
                ratio = adaptive_ratio(result, evidence.config)
        else:
            fallback = result.fallback or "unusable"
    elif config.evidence_enabled and evidence is None:
        fallback = "selector_unavailable"
    ratio = min(1.0, max(0.05, ratio))
    units: list[Unit] = []
    if draft_prefix:
        units.append(
            Unit(
                "draft",
                estimate_tokens(draft_prefix),
                config.allocator_draft_benefit,
                order=0,
            )
        )
    if summary:
        units.append(
            Unit(
                "memory",
                estimate_tokens(str(summary[0].get("content", ""))),
                config.allocator_memory_benefit,
                order=1,
            )
        )
    total = len(blocks)
    anchor_index: int | None = None
    if config.anchor and config.evidence_enabled and selector == "evidence" and total:
        candidates = range(1, total - 1) if total > 2 else range(total)
        anchor_index = max(candidates, key=lambda index: (scores[index], -index))
    for index, block in enumerate(blocks):
        units.append(
            Unit(
                f"block:{index}",
                block.tokens,
                min(1.0, scores[index]) + (total - index) * 1e-6,
                order=2 + index,
                mandatory=index == 0 or index == total - 1 or index == anchor_index,
            )
        )
    allocation = allocate(
        units,
        config=AllocatorConfig(
            enabled=True,
            budget_ratio=ratio,
            min_prompt_tokens=config.allocator_min_prompt_tokens,
            min_blocks=prefill_config.min_blocks,
            draft_benefit=config.allocator_draft_benefit,
            memory_benefit=config.allocator_memory_benefit,
        ),
    )
    allocation = dataclasses.replace(
        allocation,
        budget_ratio=ratio,
        selector=selector,
        entropy=entropy,
        top_gap=top_gap,
        fallback=fallback,
    )
    if allocation.skipped is not None:
        return _merge_prefix(working, draft_prefix), allocation

    chosen = set(allocation.selected)
    rebuilt: list[dict] = []
    if "draft" in chosen and draft_prefix:
        rebuilt.append({"role": "system", "content": draft_prefix})
    if "memory" in chosen and summary:
        rebuilt.extend(dict(message) for message in summary)
    for message_index, message in enumerate(body):
        parts = [
            block.text
            for block in blocks
            if block.message_index == message_index and f"block:{block.index}" in chosen
        ]
        if parts:
            rebuilt.append(
                {"role": message.get("role", ""), "content": "\n\n".join(parts)}
            )
    if not rebuilt:
        return _merge_prefix(working, draft_prefix), allocation
    return rebuilt, allocation


def build_prompt(
    messages: Sequence[dict],
    decisions: Sequence[Decision] = (),
    config: PromptConfig | None = None,
    *,
    query: str = "",
    registry=None,
    applied: Mapping[str, str | float | None] | None = None,
    prefill_config: PrefillConfig | None = None,
    evidence: EvidenceSelector | None = None,
) -> PromptBuild:
    """按固定顺序组装生成用 messages（全部关闭时逐字返回输入副本）。"""
    config = config if config is not None else PromptConfig()
    original = [dict(message) for message in messages]
    before_tokens = memory_tokens(original)

    working = original
    memory_summary_tokens = 0
    if config.memory_enabled:
        working = compress_with_memory(
            working, keep_turns=config.keep_turns, decisions=decisions
        )
        memory_summary_tokens = sum(
            estimate_tokens(str(message.get("content", "")))
            for message in working
            if message.get("role") == "system"
        )

    prefix = ""
    if config.draft_enabled:
        prefix = build_draft_prefix(decisions, registry)

    allocation: Allocation | None = None
    plan: PrefillPlan | None = None
    if config.allocator_enabled:
        working, allocation = _allocate_prompt(
            working,
            prefix,
            query=query,
            applied=applied or {},
            config=config,
            prefill_config=prefill_config or PrefillConfig(),
            evidence=evidence,
        )
    else:
        if prefix:
            working = _merge_prefix(working, prefix)
        if config.prefill_enabled:
            cfg = (
                prefill_config
                if prefill_config is not None
                else PrefillConfig(enabled=True)
            )
            if not cfg.enabled:
                cfg = dataclasses.replace(cfg, enabled=True)
            if config.evidence_enabled:
                cfg = dataclasses.replace(
                    cfg,
                    evidence=True,
                    adaptive=config.adaptive,
                    anchor=config.anchor,
                    evidence_config=config.evidence_config,
                )
            plan = plan_prefill(working, query, applied or {}, cfg, evidence=evidence)
            working = plan.messages

    after_tokens = memory_tokens(working)
    return PromptBuild(
        messages=working,
        before_tokens=before_tokens,
        after_tokens=after_tokens,
        memory_summary_tokens=memory_summary_tokens,
        prefill=plan,
        draft_prefix=prefix,
        allocation=allocation,
    )


__all__ = ["PromptBuild", "PromptConfig", "build_prompt"]
