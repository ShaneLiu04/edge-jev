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
from jev.kernel.prefill.blocks import estimate_tokens
from jev.kernel.prefill.planner import PrefillConfig, PrefillPlan, plan_prefill


@dataclasses.dataclass(frozen=True)
class PromptConfig:
    draft_enabled: bool = False
    memory_enabled: bool = False
    prefill_enabled: bool = False
    keep_turns: int = 2

    def __post_init__(self) -> None:
        if self.keep_turns < 1:
            raise ValueError(f"keep_turns 必须 >=1: {self.keep_turns}")


@dataclasses.dataclass(frozen=True)
class PromptBuild:
    messages: list[dict]
    before_tokens: int
    after_tokens: int
    memory_summary_tokens: int
    prefill: PrefillPlan | None
    draft_prefix: str


def _merge_prefix(messages: Sequence[dict], prefix: str) -> list[dict]:
    if not prefix:
        return [dict(message) for message in messages]
    return [{"role": "system", "content": prefix}, *[dict(m) for m in messages]]


def build_prompt(
    messages: Sequence[dict],
    decisions: Sequence[Decision] = (),
    config: PromptConfig | None = None,
    *,
    query: str = "",
    registry=None,
    applied: Mapping[str, str | float | None] | None = None,
    prefill_config: PrefillConfig | None = None,
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
        working = _merge_prefix(working, prefix)

    plan: PrefillPlan | None = None
    if config.prefill_enabled:
        cfg = prefill_config if prefill_config is not None else PrefillConfig(enabled=True)
        if not cfg.enabled:
            cfg = dataclasses.replace(cfg, enabled=True)
        plan = plan_prefill(working, query, applied or {}, cfg)
        working = plan.messages

    after_tokens = memory_tokens(working)
    return PromptBuild(
        messages=working,
        before_tokens=before_tokens,
        after_tokens=after_tokens,
        memory_summary_tokens=memory_summary_tokens,
        prefill=plan,
        draft_prefix=prefix,
    )


__all__ = ["PromptBuild", "PromptConfig", "build_prompt"]
