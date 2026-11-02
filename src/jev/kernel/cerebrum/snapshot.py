"""AR019 PPKS：持久化前缀 KV 快照客户端。

把 llama-server 的 slot KV 以"压缩后文档 + 模型指纹"为键落盘，重启后恢复，
使同文档新问题只 prefill 问题本身。保存异步执行，稳态请求不重复 save/restore。
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jev.errors import JevProtocolError
from jev.kernel.cerebrum.llama_server import LlamaServerClient
from jev.kernel.cerebrum.tiering import split_question_document

logger = logging.getLogger(__name__)

_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class SnapshotConfig:
    """快照开关与边界；默认关闭，关闭时完全不触碰磁盘/API。"""

    enabled: bool = False
    directory: Path | None = None
    max_entries: int = 4
    min_chars: int = 1000
    slot_id: int = 0

    def __post_init__(self) -> None:
        if self.max_entries < 1:
            raise ValueError("kv_snapshot_max_entries 必须 >= 1")
        if self.min_chars < 0:
            raise ValueError("kv_snapshot_min_chars 必须 >= 0")
        if self.slot_id < 0:
            raise ValueError("kv_snapshot_slot 必须 >= 0")
        if self.enabled and self.directory is None:
            raise ValueError("启用 kv_snapshot 时必须提供 kv_snapshot_dir")


def document_key(messages: list[dict[str, Any]], *, min_chars: int) -> str | None:
    """从实际发送的 prompt 提取文档并计算稳定键；短文档/无文档返回 None。"""
    text = ""
    for message in reversed(messages):
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            text = message["content"]
            break
    document, _question = split_question_document(text)
    if len(document) < min_chars:
        return None
    return hashlib.sha256(document.encode("utf-8")).hexdigest()


class SnapshotClient:
    """LlamaServerClient 的透明快照包装，chat 语义与内层完全一致。"""

    def __init__(
        self,
        inner: LlamaServerClient,
        config: SnapshotConfig,
        namespace: str,
    ) -> None:
        if not config.enabled or config.directory is None:
            raise ValueError("SnapshotClient 需要 enabled 且有目录的 SnapshotConfig")
        self._inner = inner
        self._config = config
        self._namespace = _SAFE_NAME.sub("-", namespace)[:48] or "default"
        self._directory = Path(config.directory)
        self._directory.mkdir(parents=True, exist_ok=True)
        self._restored: set[str] = set()
        self._saved: set[str] = set()
        self._inflight: set[str] = set()
        self._lock = threading.Lock()

    @property
    def base_url(self) -> str:
        return self._inner.base_url

    @property
    def inner(self) -> LlamaServerClient:
        return self._inner

    def close(self) -> None:
        self._inner.close()

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
        key = document_key(messages, min_chars=self._config.min_chars)
        status = "off"
        if key is not None:
            filename = self._filename(key)
            path = self._directory / filename
            if key not in self._restored and path.exists():
                status = self._try_restore(key, filename, path)
            else:
                status = "miss"

        data = self._inner.chat(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=stop,
            timeout_s=timeout_s,
            extra_body=extra_body,
        )
        if key is not None:
            path = self._directory / self._filename(key)
            if (
                not path.exists()
                and key not in self._saved
                and status not in {"save", "error"}
            ):
                with self._lock:
                    launch = key not in self._inflight
                    if launch:
                        self._inflight.add(key)
                if launch:
                    status = "save"
                    thread = threading.Thread(
                        target=self._save_async,
                        args=(key, self._filename(key)),
                        name=f"jev-kv-save-{self._namespace}",
                        daemon=True,
                    )
                    thread.start()
        if isinstance(data, dict) and status != "off":
            data["_jev_snapshot"] = status
        return data

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
        """流式不启用快照：语义/时延与内核直连一致。"""
        return self._inner.chat_stream(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=stop,
            timeout_s=timeout_s,
            extra_body=extra_body,
        )

    def _filename(self, key: str) -> str:
        return f"{self._namespace}-{key[:32]}.bin"

    def _try_restore(self, key: str, filename: str, path: Path) -> str:
        try:
            result = self._inner.slot_restore(
                filename, slot_id=self._config.slot_id
            )
        except JevProtocolError as exc:
            logger.warning("KV 快照恢复失败，删除并回退正常 prefill：%s", exc)
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return "error"
        except Exception as exc:  # noqa: BLE001 - 任何快照异常都不得阻断正常请求
            logger.warning("KV 快照恢复异常，回退正常 prefill：%s", exc)
            return "error"
        if result.get("n_restored", 0) <= 0:
            return "error"
        with self._lock:
            self._restored.add(key)
        logger.info(
            "KV 快照已恢复 namespace=%s tokens=%s restore_ms=%s",
            self._namespace,
            result.get("n_restored"),
            (result.get("timings") or {}).get("restore_ms"),
        )
        return "restore"

    def _save_async(self, key: str, filename: str) -> None:
        try:
            result = self._inner.slot_save(filename, slot_id=self._config.slot_id)
            n_saved = int(result.get("n_saved", 0))
            if n_saved <= 0:
                logger.warning("KV 快照保存未写入 token：%s", filename)
                return
            with self._lock:
                self._saved.add(key)
            logger.info(
                "KV 快照已保存 namespace=%s file=%s tokens=%s save_ms=%s",
                self._namespace,
                filename,
                n_saved,
                (result.get("timings") or {}).get("save_ms"),
            )
            self._evict()
        except Exception as exc:  # noqa: BLE001 - 后台保存失败不影响请求
            logger.warning("KV 快照后台保存失败 %s：%s", filename, exc)
        finally:
            with self._lock:
                self._inflight.discard(key)

    def _evict(self) -> None:
        files = sorted(
            self._directory.glob(f"{self._namespace}-*.bin"),
            key=lambda item: item.stat().st_mtime,
        )
        for stale in files[: max(0, len(files) - self._config.max_entries)]:
            try:
                stale.unlink()
            except OSError:
                continue


@dataclass
class SnapshotStats:
    """可选诊断计数（测试/实验用），不参与线上路由。"""

    restores: int = 0
    saves: int = 0
    errors: int = 0
    events: list[str] = field(default_factory=list)
