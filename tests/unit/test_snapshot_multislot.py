from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from jev.errors import JevProtocolError
from jev.kernel.cerebrum.snapshot import (
    SnapshotClient,
    SnapshotConfig,
    document_key,
)


class _SlotInner:
    def __init__(self) -> None:
        self.saves: list[tuple[str, int]] = []
        self.restores: list[tuple[str, int]] = []
        self.chats: list[dict[str, Any]] = []
        self.restore_result: dict[str, Any] = {"n_restored": 3}
        self.save_result: dict[str, Any] = {"n_saved": 3}
        self.restore_error: Exception | None = None
        self.save_dir: Path | None = None
        self.block: threading.Event | None = None
        self.entered: threading.Event | None = None

    @property
    def base_url(self) -> str:
        return "http://fake"

    def chat(self, messages, **kwargs):
        self.chats.append(dict(kwargs))
        if self.entered is not None:
            self.entered.set()
        if self.block is not None:
            self.block.wait(timeout=2.0)
        return {"choices": [{"message": {"content": "ok"}}], "usage": {}}

    def chat_stream(self, messages, **kwargs):
        yield {"choices": [{"delta": {"content": "ok"}}]}

    def slot_save(self, filename, *, slot_id=0):
        self.saves.append((filename, slot_id))
        if self.save_dir is not None:
            (self.save_dir / filename).write_bytes(b"snapshot")
        return self.save_result

    def slot_restore(self, filename, *, slot_id=0):
        if self.restore_error is not None:
            raise self.restore_error
        self.restores.append((filename, slot_id))
        return self.restore_result

    def close(self) -> None:
        pass


def _messages(document: str = "d" * 1500, question: str = "问题一"):
    return [{"role": "user", "content": f"{document}\n问题：{question}"}]


def _wait(predicate, timeout_s: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_config_defaults_to_configured_slot(tmp_path: Path) -> None:
    inner = _SlotInner()
    config = SnapshotConfig(
        enabled=True, directory=tmp_path, min_chars=100, slot_id=2
    )
    client = SnapshotClient(inner, config, "ns")
    key = document_key(_messages(), min_chars=100)
    assert key is not None
    path = tmp_path / f"ns-{key[:32]}.bin"
    path.write_bytes(b"x")

    client.chat(_messages())

    assert inner.restores == [(f"ns-{key[:32]}.bin", 2)]


def test_per_request_slot_restores_each_slot_once(tmp_path: Path) -> None:
    inner = _SlotInner()
    config = SnapshotConfig(
        enabled=True,
        directory=tmp_path,
        min_chars=100,
        per_request_slot=True,
    )
    client = SnapshotClient(inner, config, "ns")
    key = document_key(_messages(), min_chars=100)
    assert key is not None
    filename = f"ns-{key[:32]}.bin"
    (tmp_path / filename).write_bytes(b"x")

    first = client.chat(_messages(), extra_body={"id_slot": 1})
    second = client.chat(_messages(), extra_body={"id_slot": 2})
    third = client.chat(_messages(), extra_body={"id_slot": 2})

    assert first["_jev_snapshot"] == "restore"
    assert second["_jev_snapshot"] == "restore"
    assert third["_jev_snapshot"] == "miss"
    assert inner.restores == [(filename, 1), (filename, 2)]
    assert inner.chats[0]["extra_body"] == {"id_slot": 1}


def test_per_request_slot_save_uses_request_slot(tmp_path: Path) -> None:
    inner = _SlotInner()
    inner.save_dir = tmp_path
    config = SnapshotConfig(
        enabled=True,
        directory=tmp_path,
        min_chars=100,
        per_request_slot=True,
    )
    client = SnapshotClient(inner, config, "ns")

    client.chat(_messages(), extra_body={"id_slot": 3})

    assert _wait(lambda: len(inner.saves) == 1)
    assert inner.saves[0][1] == 3


def test_busy_slot_skips_restore(tmp_path: Path) -> None:
    inner = _SlotInner()
    inner.entered = threading.Event()
    inner.block = threading.Event()
    config = SnapshotConfig(
        enabled=True,
        directory=tmp_path,
        min_chars=100,
        per_request_slot=True,
    )
    client = SnapshotClient(inner, config, "ns")
    key = document_key(_messages(), min_chars=100)
    other = document_key(_messages(document="e" * 1500), min_chars=100)
    assert key is not None and other is not None
    (tmp_path / f"ns-{key[:32]}.bin").write_bytes(b"x")
    (tmp_path / f"ns-{other[:32]}.bin").write_bytes(b"x")

    results: list[dict[str, Any]] = []
    first = threading.Thread(
        target=lambda: results.append(
            client.chat(_messages(), extra_body={"id_slot": 0})
        )
    )
    first.start()
    assert inner.entered.wait(timeout=2.0)

    second = client.chat(
        _messages(document="e" * 1500), extra_body={"id_slot": 0}
    )
    inner.block.set()
    first.join(timeout=2.0)

    assert second["_jev_snapshot"] == "miss"
    assert [slot for _, slot in inner.restores] == [0]


def test_multislot_restore_error_keeps_file(tmp_path: Path) -> None:
    inner = _SlotInner()
    inner.restore_error = JevProtocolError("slot busy")
    config = SnapshotConfig(
        enabled=True,
        directory=tmp_path,
        min_chars=100,
        per_request_slot=True,
    )
    client = SnapshotClient(inner, config, "ns")
    key = document_key(_messages(), min_chars=100)
    assert key is not None
    path = tmp_path / f"ns-{key[:32]}.bin"
    path.write_bytes(b"x")

    response = client.chat(_messages(), extra_body={"id_slot": 1})

    assert response["_jev_snapshot"] == "error"
    assert path.exists()
    assert len(inner.chats) == 1


def test_lru_eviction_allows_resaving(tmp_path: Path) -> None:
    inner = _SlotInner()
    inner.save_dir = tmp_path
    config = SnapshotConfig(
        enabled=True, directory=tmp_path, max_entries=1, min_chars=100
    )
    client = SnapshotClient(inner, config, "ns")

    client.chat(_messages(document="a" * 1500), extra_body={"id_slot": 0})
    assert _wait(lambda: len(inner.saves) == 1)
    time.sleep(0.2)
    client.chat(_messages(document="b" * 1500), extra_body={"id_slot": 0})
    assert _wait(lambda: len(inner.saves) == 2)
    time.sleep(0.2)
    client.chat(_messages(document="a" * 1500), extra_body={"id_slot": 0})
    assert _wait(lambda: len(inner.saves) == 3)


def test_kernel_builder_allows_parallel_and_marks_per_request(tmp_path: Path) -> None:
    from jev.kernel.kernel import _build_snapshot_config

    config = _build_snapshot_config(
        {
            "cerebrum": {
                "kv_snapshot_dir": str(tmp_path / "snap"),
                "parallel": 4,
                "slot_scheduler_enabled": True,
            }
        }
    )
    assert config.enabled
    assert config.per_request_slot
