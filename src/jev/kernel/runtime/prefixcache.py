"""前缀缓存策略（AR007 FR7-2，design §3.2）。

纯函数：决定是否使用「稳定原文前缀 + KV 复用」而非按问题变化的自适应压缩。
"""

from __future__ import annotations


def use_stable_prefix(
    prefix_cache: bool,
    session_affinity: bool,
    session_id: str,
) -> bool:
    """只有开启 prefix_cache 且（不要求会话亲和 或 有会话键）时才用稳定前缀。"""
    if not prefix_cache:
        return False
    if not session_affinity:
        return False
    return bool(str(session_id).strip())


__all__ = ["use_stable_prefix"]
