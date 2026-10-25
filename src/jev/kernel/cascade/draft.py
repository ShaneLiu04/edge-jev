"""PRE 草稿注入与多轮历史压缩（AR002 FR2-3，路径②/③）。

原理：把 PRE 批的结构化结论压成 system 前缀（跨厂商通用，避免依赖
chat template 的特殊 token），并把超期历史轮次截断为摘要，降低 full 路径
的 prompt token（成本门 ≥15%）。仅当配置启用时生效；默认关闭保持 AR001
的逐字行为。token 估算沿用 §4.5 的确定性口径：4 字符 ≈ 1 token（中文
保守、离线可复现，不引入 tokenizer 依赖）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from jev.kernel.decisions.framework import Decision, DecisionRegistry

KEEP_TURNS_DEFAULT = 2
SUMMARY_MAX_CHARS = 120
CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class DraftConfig:
    """草稿注入配置（[cascade] draft_injection / keep_turns）。"""

    enabled: bool = False
    keep_turns: int = KEEP_TURNS_DEFAULT
    summary_max_chars: int = SUMMARY_MAX_CHARS


def estimate_tokens(text: str) -> int:
    """确定性 token 粗估（向上取整；空串为 0）。"""
    if not text:
        return 0
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def estimate_messages_tokens(messages: Sequence[dict]) -> int:
    return sum(estimate_tokens(str(message.get("content", ""))) for message in messages)


def summarize_message(message: dict, *, max_chars: int = SUMMARY_MAX_CHARS) -> dict:
    """单条历史消息压缩：保留角色与语义开头，硬截断到 max_chars。"""
    content = str(message.get("content", ""))
    if len(content) <= max_chars:
        return dict(message)
    return {**message, "content": content[:max_chars] + "…"}


def compress_history(
    messages: Sequence[dict], *, keep_turns: int = KEEP_TURNS_DEFAULT
) -> list[dict]:
    """保留最近 keep_turns 个轮次原文；更早轮次逐条压缩为摘要。

    轮次边界：每条 user 消息开启新一轮（assistant/system 归属当前轮）；
    无 user 的输入整体视为一轮。
    """
    if keep_turns < 1:
        raise ValueError("keep_turns 必须 ≥ 1")
    spans: list[int] = [index for index, m in enumerate(messages) if m.get("role") == "user"]
    if not spans:
        return [summarize_message(m) for m in messages]
    keep_from = spans[-keep_turns] if len(spans) >= keep_turns else spans[0]
    return [
        dict(message) if index >= keep_from else summarize_message(message)
        for index, message in enumerate(messages)
    ]


def build_draft_prefix(
    decisions: Sequence[Decision],
    registry: DecisionRegistry | None,
    *,
    max_items: int = 6,
) -> str:
    """PRE 结构化结论 → 单行紧凑 system 前缀（applied 语义值）。"""
    applied: list[str] = []
    for decision in decisions:
        point = None
        if registry is not None:
            try:
                point = registry.get(decision.point_id)
            except Exception:  # noqa: BLE001 - 未注册点跳过，草稿不阻断
                point = None
        value = decision.applied_value(point) if point is not None else decision.value
        applied.append(f"{decision.point_id}={value}")
        if len(applied) >= max_items:
            break
    if not applied:
        return ""
    return "系统预判：" + "；".join(applied)


def apply_draft(
    messages: Sequence[dict],
    prefix: str,
    *,
    keep_turns: int = KEEP_TURNS_DEFAULT,
) -> list[dict]:
    """组装注入后的 messages：system 前缀 + 压缩历史（无前缀时仅压缩）。"""
    history = compress_history(messages, keep_turns=keep_turns)
    if not prefix:
        return history
    return [{"role": "system", "content": prefix}, *history]


__all__ = [
    "CHARS_PER_TOKEN",
    "KEEP_TURNS_DEFAULT",
    "SUMMARY_MAX_CHARS",
    "DraftConfig",
    "apply_draft",
    "build_draft_prefix",
    "compress_history",
    "estimate_messages_tokens",
    "estimate_tokens",
    "summarize_message",
]
