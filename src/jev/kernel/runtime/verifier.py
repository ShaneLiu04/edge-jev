"""大脑验证器（AR003 FR1-3/FR1-4，design §4.2.2(4)）。

只依赖 `ChatFn` 协议（LlamaServerClient 天然满足），不直接持有后端。
解析失败/超时/不可达一律按 REJECT（安全侧），不重试；token 计入台账。
"""

from __future__ import annotations

import dataclasses
import logging
import re
import time
from collections.abc import Mapping
from typing import Protocol

from jev.errors import JevError

logger = logging.getLogger(__name__)

_VERDICT_RE = re.compile(r"^\s*(ACCEPT|REJECT)\b", re.IGNORECASE | re.MULTILINE)
_MAX_APPLIED_ITEMS = 8

_SYSTEM_PROMPT = (
    "You are a strict verifier for a cost-aware cascade. Given the user request, "
    "a candidate answer, and structured signals, decide whether the candidate "
    "fully and safely answers the request.\n"
    "Reply on the first line with exactly one word: ACCEPT or REJECT.\n"
    "On the second line, give one short reason.\n"
    "REJECT if the answer is incomplete, unsafe, off-topic, or contradicts the signals."
)


class ChatFn(Protocol):
    """验证器所需的最小大脑接口（与 LlamaServerClient.chat 兼容）。"""

    def chat(
        self,
        messages: list[dict],
        *,
        max_tokens: int = 16,
        temperature: float = 0.0,
        stop: list[str] | None = None,
        timeout_s: float | None = None,
        extra_body: dict | None = None,
    ) -> dict: ...


@dataclasses.dataclass(frozen=True)
class VerifyResult:
    verdict: str  # accept | reject
    reason: str
    tokens: int
    unavailable: bool
    latency_ms: float


def parse_verdict(text: str) -> tuple[str, str]:
    """严格解析：首行首词必须是 ACCEPT/REJECT；否则 reject（不猜测）。"""
    content = (text or "").strip()
    if not content:
        return "reject", "验证响应为空"
    match = _VERDICT_RE.match(content)
    if match is None:
        return "reject", f"无法解析验证响应: {content[:80]}"
    verdict = match.group(1).lower()
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    reason = lines[1] if len(lines) > 1 else ""
    return verdict, reason


def _render_applied(applied: Mapping[str, str | float | None]) -> str:
    items = list(applied.items())[:_MAX_APPLIED_ITEMS]
    if not items:
        return "（无）"
    return "; ".join(f"{name}={value}" for name, value in items)


def _build_messages(request_text: str, draft: str,
                    applied: Mapping[str, str | float | None]) -> list[dict]:
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"结构化信号: {_render_applied(applied)}\n\n"
                f"用户请求:\n{request_text}\n\n"
                f"候选答案:\n{draft}\n\n"
                "请裁决。"
            ),
        },
    ]


class BrainVerifier:
    """真实大脑验证器（单次调用、确定性采样、安全侧失败）。"""

    def __init__(self, chat_fn: ChatFn, *, max_tokens: int = 16) -> None:
        self._chat = chat_fn
        self._max_tokens = max_tokens

    def verify(
        self,
        request_text: str,
        draft: str,
        applied: Mapping[str, str | float | None],
        *,
        timeout_s: float | None = None,
    ) -> VerifyResult:
        messages = _build_messages(request_text, draft, applied)
        started = time.perf_counter()
        try:
            response = self._chat.chat(
                messages,
                max_tokens=self._max_tokens,
                temperature=0.0,
                stop=None,
                timeout_s=timeout_s,
                extra_body={"cache_prompt": True},
            )
        except JevError as exc:
            latency_ms = (time.perf_counter() - started) * 1000.0
            logger.warning("验证器不可用，按 REJECT 处理: %s", exc)
            return VerifyResult(
                verdict="reject",
                reason=f"验证器不可用: {exc}",
                tokens=0,
                unavailable=True,
                latency_ms=latency_ms,
            )
        latency_ms = (time.perf_counter() - started) * 1000.0
        content = _content_of(response)
        usage = response.get("usage") if isinstance(response, dict) else None
        tokens = int(usage.get("completion_tokens", 0)) if isinstance(usage, dict) else 0
        verdict, reason = parse_verdict(content)
        return VerifyResult(
            verdict=verdict,
            reason=reason,
            tokens=tokens,
            unavailable=False,
            latency_ms=latency_ms,
        )


def _content_of(response: object) -> str:
    if not isinstance(response, dict):
        return ""
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    return content if isinstance(content, str) else ""


__all__ = ["BrainVerifier", "ChatFn", "VerifyResult", "parse_verdict"]
