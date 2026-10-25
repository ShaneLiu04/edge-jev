"""前缀指纹（AR003 FR2-1，design §4.2.2(6)）。

只对「历史消息」（末条 user 之前）取指纹：同一会话的多轮请求共享前缀，
便于 llama-server prompt cache / 槽复用，同时不会因当前问题变化而失效。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence


def prefix_fingerprint(messages: Sequence[dict], *, content_truncate: int = 512) -> str:
    """返回 64 位十六进制指纹；历史为空时返回固定常量指纹。"""
    if content_truncate < 0:
        raise ValueError(f"content_truncate 不得为负: {content_truncate}")
    history = list(messages[:-1]) if len(messages) > 1 else []
    canonical = [
        {
            "role": str(message.get("role", "")),
            "content": str(message.get("content", ""))[:content_truncate],
        }
        for message in history
    ]
    payload = json.dumps(canonical, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = ["prefix_fingerprint"]
