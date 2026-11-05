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
    per_request_slot: bool = False

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
        self._restored: set[tuple[str, int]] = set()
        self._saved: set[str] = set()
        self._inflight: set[str] = set()
        self._active: dict[int, int] = {}
        self._active_keys: dict[tuple[int, str], int] = {}
        self._lock = threading.Lock()
        self._restore_cond = threading.Condition(self._lock)
        self._restoring: set[int] = set()

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
        slot = self._slot_for(extra_body)
        key_id = key or ""
        self._enter(slot, key_id)
        self._wait_slot_idle(slot)
        status = "off"
        if key is not None:
            filename = self._filename(key)
            path = self._directory / filename
            if path.exists() and self._acquire_restore(key, slot):
                try:
                    status = self._try_restore(key, filename, path, slot)
                finally:
                    self._release_restore(slot)
            else:
                status = "miss"

        try:
            data = self._inner.chat(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stop=stop,
                timeout_s=timeout_s,
                extra_body=extra_body,
            )
        finally:
            self._leave(slot, key_id)
        if key is not None:
            path = self._directory / self._filename(key)
            if not path.exists() and status not in {"save", "error"}:
                with self._restore_cond:
                    launch = (
                        key not in self._saved
                        and key not in self._inflight
                        and self._active.get(slot, 0) == 0
                        and slot not in self._restoring
                    )
                    if launch:
                        self._inflight.add(key)
                        self._restoring.add(slot)
                if launch:
                    status = "save"
                    thread = threading.Thread(
                        target=self._save_async,
                        args=(key, self._filename(key), slot),
                        name=f"jev-kv-save-{self._namespace}",
                        daemon=True,
                    )
                    try:
                        thread.start()
                    except RuntimeError:
                        with self._restore_cond:
                            self._inflight.discard(key)
                            self._restoring.discard(slot)
                            self._restore_cond.notify_all()
                        status = "error"
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

    def _slot_for(self, extra_body: dict[str, Any] | None) -> int:
        if self._config.per_request_slot and extra_body is not None:
            requested = extra_body.get("id_slot")
            if isinstance(requested, int) and requested >= 0:
                return requested
        return self._config.slot_id

    def _enter(self, slot: int, key: str) -> None:
        with self._lock:
            self._active[slot] = self._active.get(slot, 0) + 1
            pair = (slot, key)
            self._active_keys[pair] = self._active_keys.get(pair, 0) + 1

    def _leave(self, slot: int, key: str) -> None:
        with self._lock:
            remaining = self._active.get(slot, 0) - 1
            if remaining <= 0:
                self._active.pop(slot, None)
            else:
                self._active[slot] = remaining
            pair = (slot, key)
            key_remaining = self._active_keys.get(pair, 0) - 1
            if key_remaining <= 0:
                self._active_keys.pop(pair, None)
            else:
                self._active_keys[pair] = key_remaining

    def _wait_slot_idle(self, slot: int) -> None:
        """等待该槽的异步保存/恢复结束，避免新请求读到半写快照。"""
        with self._restore_cond:
            while slot in self._restoring:
                self._restore_cond.wait(timeout=30.0)

    def _acquire_restore(self, key: str, slot: int) -> bool:
        """仅当该槽只承载当前请求时恢复；同槽其它在途请求一律跳过。"""
        with self._restore_cond:
            while slot in self._restoring:
                self._restore_cond.wait(timeout=30.0)
            if (key, slot) in self._restored:
                return False
            other_document = any(
                active_slot == slot and active_key != key and count > 0
                for (active_slot, active_key), count in self._active_keys.items()
            )
            if other_document:
                return False
            self._restoring.add(slot)
            return True

    def _release_restore(self, slot: int) -> None:
        with self._restore_cond:
            self._restoring.discard(slot)
            self._restore_cond.notify_all()

    def _filename(self, key: str) -> str:
        return f"{self._namespace}-{key[:32]}.bin"

    def _try_restore(
        self, key: str, filename: str, path: Path, slot: int
    ) -> str:
        try:
            result = self._inner.slot_restore(filename, slot_id=slot)
        except JevProtocolError as exc:
            logger.warning("KV 快照恢复失败，回退正常 prefill：%s", exc)
            if not self._config.per_request_slot:
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
            self._restored.add((key, slot))
        logger.info(
            "KV 快照已恢复 namespace=%s slot=%s tokens=%s restore_ms=%s",
            self._namespace,
            slot,
            result.get("n_restored"),
            (result.get("timings") or {}).get("restore_ms"),
        )
        return "restore"

    def _save_async(self, key: str, filename: str, slot: int) -> None:
        try:
            result = self._inner.slot_save(filename, slot_id=slot)
            n_saved = int(result.get("n_saved", 0))
            if n_saved <= 0:
                logger.warning("KV 快照保存未写入 token：%s", filename)
                return
            with self._lock:
                self._saved.add(key)
            logger.info(
                "KV 快照已保存 namespace=%s slot=%s file=%s tokens=%s save_ms=%s",
                self._namespace,
                slot,
                filename,
                n_saved,
                (result.get("timings") or {}).get("save_ms"),
            )
            self._evict()
        except Exception as exc:  # noqa: BLE001 - 后台保存失败不影响请求
            logger.warning("KV 快照后台保存失败 %s：%s", filename, exc)
        finally:
            with self._restore_cond:
                self._inflight.discard(key)
                self._restoring.discard(slot)
                self._restore_cond.notify_all()

    def _evict(self) -> None:
        files: list[tuple[float, Path]] = []
        for item in self._directory.glob(f"{self._namespace}-*.bin"):
            try:
                files.append((item.stat().st_mtime, item))
            except OSError:
                continue
        files.sort(key=lambda entry: entry[0])
        stale = files[: max(0, len(files) - self._config.max_entries)]
        stale_names = {item.name for _mtime, item in stale}
        for _mtime, item in stale:
            try:
                item.unlink()
            except OSError:
                continue
        if not stale_names:
            return
        with self._lock:
            self._saved = {
                key for key in self._saved if self._filename(key) not in stale_names
            }
            self._restored = {
                (key, slot)
                for key, slot in self._restored
                if self._filename(key) not in stale_names
            }


@dataclass
class SnapshotStats:
    """可选诊断计数（测试/实验用），不参与线上路由。"""

    restores: int = 0
    saves: int = 0
    errors: int = 0
    events: list[str] = field(default_factory=list)
