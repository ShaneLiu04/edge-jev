"""块显著性打分（AR003 FR3-1，design §4.2.2(7)）。

确定性特征融合（无模型、无随机）：
    score = 0.5·lexical_overlap + 0.3·decision_signal + 0.2·position_prior
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from jev.kernel.prefill.blocks import Block

_WEIGHT_LEXICAL = 0.5
_WEIGHT_DECISION = 0.3
_WEIGHT_POSITION = 0.2

_WORD = re.compile(r"[a-z0-9_]+")
_CJK = re.compile(r"[\u4e00-\u9fff]+")

_DECISION_KEYWORDS: dict[str, dict[str, tuple[str, ...]]] = {
    "needs_tool": {
        "tool": ("tool", "function", "api", "调用", "工具", "接口"),
        "no_tool": ("聊天", "闲聊", "chat"),
    },
    "format": {
        "code": ("code", "代码", "python", "函数", "def ", "```"),
        "table": ("table", "表格", "|"),
        "list": ("list", "列表", "1.", "- "),
        "text": (),
    },
    "needs_reasoning": {
        "reasoning": ("推理", "证明", "分析", "why", "derive", "证明", "逻辑"),
        "no_reasoning": (),
    },
    "complexity": {
        "high": ("架构", "architecture", "步骤", "step", "设计", "方案"),
        "medium": (),
        "low": (),
    },
    "safety": {
        "unsafe": ("风险", "risk", "安全", "safety", "隐私", "违禁"),
        "safe": (),
    },
}


def tokens_of(text: str) -> set[str]:
    """英文词 + 中文 2-gram（确定性、大小写归一）。"""
    lowered = text.lower()
    words = set(_WORD.findall(lowered))
    for sequence in _CJK.findall(lowered):
        if len(sequence) == 1:
            words.add(sequence)
            continue
        words.update(sequence[index:index + 2] for index in range(len(sequence) - 1))
    return words


def lexical_overlap(text: str, query: str) -> float:
    """Jaccard 相似度；任一侧为空 → 0。"""
    left = tokens_of(text)
    right = tokens_of(query)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def decision_signal(text: str, applied: Mapping[str, str | float | None]) -> float:
    """块文本命中 applied 语义关键词的比例（0–1，最多记 2 次命中）。"""
    lowered = text.lower()
    hits = 0
    for point_id, value in applied.items():
        if value is None:
            continue
        table = _DECISION_KEYWORDS.get(point_id)
        if not table:
            continue
        keywords = table.get(str(value), ())
        if any(keyword.lower() in lowered for keyword in keywords):
            hits += 1
    return min(1.0, hits / 2.0)


def position_prior(index: int, total: int) -> float:
    """首末块 1.0，中间线性衰减。"""
    if total <= 1:
        return 1.0
    if index == 0 or index == total - 1:
        return 1.0
    middle = (total - 1) / 2.0
    return max(0.0, 1.0 - abs(index - middle) / middle)


def score_blocks(
    blocks: Sequence[Block],
    query: str,
    applied: Mapping[str, str | float | None],
) -> list[float]:
    total = len(blocks)
    return [
        _WEIGHT_LEXICAL * lexical_overlap(block.text, query)
        + _WEIGHT_DECISION * decision_signal(block.text, applied)
        + _WEIGHT_POSITION * position_prior(block.index, total)
        for block in blocks
    ]


__all__ = [
    "decision_signal",
    "lexical_overlap",
    "position_prior",
    "score_blocks",
    "tokens_of",
]
