"""长程记忆（AR003 FR4-3，design §4.2.2(9)）。

保留最近 keep_turns 轮原文；更早轮次合并为一条确定性 system 摘要
（每轮 ≤120 字 + 决策关键词），不调用模型（可复现、零额外成本）。
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from jev.kernel.decisions.framework import Decision

SUMMARY_MAX_CHARS = 120
CHARS_PER_TOKEN = 4


def _estimate(text: str) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) / CHARS_PER_TOKEN))


def memory_tokens(messages: Sequence[dict]) -> int:
    return sum(_estimate(str(message.get("content", ""))) for message in messages)


def _keywords(decisions: Sequence[Decision] | None) -> str:
    if not decisions:
        return ""
    pairs = [f"{decision.point_id}={decision.value}" for decision in decisions[:4]]
    return "；" + "，".join(pairs) if pairs else ""


def _summarize_content(content: str, keywords: str) -> str:
    room = max(20, SUMMARY_MAX_CHARS - len(keywords))
    text = content.strip().replace("\n", " ")
    if len(text) > room:
        text = text[:room] + "…"
    return text + keywords


def compress_with_memory(
    messages: Sequence[dict],
    *,
    keep_turns: int = 2,
    decisions: Sequence[Decision] | None = None,
) -> list[dict]:
    """压缩历史：最近 keep_turns 轮原文 + 更早轮次的单条 system 摘要。"""
    if keep_turns < 1:
        raise ValueError(f"keep_turns 必须 ≥1: {keep_turns}")
    items = list(messages)
    user_positions = [
        index for index, message in enumerate(items) if message.get("role") == "user"
    ]
    if len(user_positions) <= keep_turns:
        return [dict(message) for message in items]
    keep_from = user_positions[-keep_turns]
    older = items[:keep_from]
    kept = [dict(message) for message in items[keep_from:]]
    keywords = _keywords(decisions)
    lines: list[str] = []
    for message in older:
        role = str(message.get("role", ""))
        content = str(message.get("content", ""))
        if not content.strip():
            continue
        lines.append(f"{role}: {_summarize_content(content, keywords)}")
    if not lines:
        return kept
    summary = {"role": "system", "content": "历史摘要：\n" + "\n".join(lines)}
    return [summary, *kept]


__all__ = [
    "CHARS_PER_TOKEN",
    "SUMMARY_MAX_CHARS",
    "compress_with_memory",
    "memory_tokens",
]
