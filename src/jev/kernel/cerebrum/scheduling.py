"""AR022 并行槽调度：同一文档的请求在多个 slot 间轮转，兼顾前缀复用与并发。"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from jev.kernel.cerebrum.llama_server import LlamaServerClient
from jev.kernel.cerebrum.tiering import split_question_document


@dataclass(frozen=True)
class SlotSchedulerConfig:
    enabled: bool = False
    slots: int = 1
    max_documents: int = 1024

    def __post_init__(self) -> None:
        if self.slots < 1:
            raise ValueError("slot_scheduler.slots 必须 >= 1")
        if self.enabled and self.slots < 2:
            raise ValueError("启用 slot_scheduler 时 slots 必须 >= 2")
        if self.max_documents < 1:
            raise ValueError("slot_scheduler.max_documents 必须 >= 1")


def _document_key(messages: list[dict[str, Any]]) -> str:
    text = ""
    for message in reversed(messages):
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            text = message["content"]
            break
    document, _question = split_question_document(text)
    source = document or text
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


class SlotScheduler:
    """per-document 轮转：连续请求分散到不同 slot，每个 slot 各自保留前缀 KV。"""

    def __init__(self, config: SlotSchedulerConfig) -> None:
        self._config = config
        self._counters: OrderedDict[str, int] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._config.enabled and self._config.slots > 1

    def slot_for(self, messages: list[dict[str, Any]]) -> int | None:
        if not self.enabled:
            return None
        key = _document_key(messages)
        with self._lock:
            index = self._counters.get(key, 0)
            if key in self._counters:
                self._counters.move_to_end(key)
            elif len(self._counters) >= self._config.max_documents:
                self._counters.popitem(last=False)
            self._counters[key] = (index + 1) % self._config.slots
        return index


class ScheduledClient:
    """透明客户端包装：chat/stream 注入 id_slot，其余语义与内层一致。"""

    def __init__(self, inner: LlamaServerClient, scheduler: SlotScheduler) -> None:
        self._inner = inner
        self._scheduler = scheduler

    @property
    def base_url(self) -> str:
        return self._inner.base_url

    def close(self) -> None:
        self._inner.close()

    def _with_slot(
        self, messages: list[dict[str, Any]], extra_body: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        slot = self._scheduler.slot_for(messages)
        if slot is None:
            return extra_body
        body = dict(extra_body or {})
        body["id_slot"] = slot
        return body

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 512,
        temperature: float = 0.7,
        stop: list[str] | None = None,
        timeout_s: float | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._inner.chat(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=stop,
            timeout_s=timeout_s,
            extra_body=self._with_slot(messages, extra_body),
        )

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 512,
        temperature: float = 0.7,
        stop: list[str] | None = None,
        timeout_s: float | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        return self._inner.chat_stream(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=stop,
            timeout_s=timeout_s,
            extra_body=self._with_slot(messages, extra_body),
        )
