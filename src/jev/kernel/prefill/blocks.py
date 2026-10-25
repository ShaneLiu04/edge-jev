"""Prompt 块分割（AR003 FR3-1，design §4.2.2(7)）。

确定性切分：按段落（\\n\\n）→ 超长段落按句号切分 → 兜底按字符硬切。
单块估算 token ≤ block_tokens（4 字符 ≈ 1 token 的保守口径）。
"""

from __future__ import annotations

import dataclasses
import math
import re
from collections.abc import Sequence

CHARS_PER_TOKEN = 4
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?\.])\s*")


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) / CHARS_PER_TOKEN))


@dataclasses.dataclass(frozen=True)
class Block:
    index: int
    message_index: int
    role: str
    text: str
    tokens: int


def _split_text(text: str, block_tokens: int) -> list[str]:
    if estimate_tokens(text) <= block_tokens:
        return [text]
    pieces = [piece for piece in _SENTENCE_SPLIT.split(text) if piece]
    chunks: list[str] = []
    current = ""
    cap_chars = block_tokens * CHARS_PER_TOKEN
    for piece in pieces:
        candidate = current + piece
        if current and estimate_tokens(candidate) > block_tokens:
            chunks.append(current)
            current = piece
        else:
            current = candidate
        while estimate_tokens(current) > block_tokens:
            chunks.append(current[:cap_chars])
            current = current[cap_chars:]
    if current:
        chunks.append(current)
    return chunks or [text[:cap_chars]]


def split_blocks(messages: Sequence[dict], *, block_tokens: int = 256) -> list[Block]:
    """按消息顺序切块；空块丢弃；index 单调递增。"""
    if block_tokens < 1:
        raise ValueError(f"block_tokens 必须 ≥1: {block_tokens}")
    blocks: list[Block] = []
    for message_index, message in enumerate(messages):
        role = str(message.get("role", ""))
        content = str(message.get("content", ""))
        for paragraph in content.split("\n\n"):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            for chunk in _split_text(paragraph, block_tokens):
                chunk = chunk.strip()
                if not chunk:
                    continue
                blocks.append(
                    Block(
                        index=len(blocks),
                        message_index=message_index,
                        role=role,
                        text=chunk,
                        tokens=estimate_tokens(chunk),
                    )
                )
    return blocks


__all__ = ["Block", "CHARS_PER_TOKEN", "estimate_tokens", "split_blocks"]
