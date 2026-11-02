from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from jev.errors import JevProtocolError
from jev.kernel.cerebrum.llama_server import LlamaServerManager
from jev.kernel.cerebrum.snapshot import (
    SnapshotClient,
    SnapshotConfig,
    document_key,
)


class _FakeInner:
    def __init__(self) -> None:
        self.saves: list[str] = []
        self.restores: list[str] = []
        self.chats: list[list[dict[str, Any]]] = []
        self.restore_result: dict[str, Any] = {"n_restored": 3}
        self.save_result: dict[str, Any] = {"n_saved": 3}
        self.restore_error: Exception | None = None
        self.save_dir: Path | None = None

    @property
    def base_url(self) -> str:
        return "http://fake"

    def chat(self, messages, **kwargs):
        self.chats.append(messages)
        return {"choices": [{"message": {"content": "ok"}}], "usage": {}}

    def chat_stream(self, messages, **kwargs):
        yield {"choices": [{"delta": {"content": "ok"}}]}

    def slot_save(self, filename, *, slot_id=0):
        self.saves.append(filename)
        if self.save_dir is not None:
            (self.save_dir / filename).write_bytes(b"snapshot")
        return self.save_result

    def slot_restore(self, filename, *, slot_id=0):
        if self.restore_error is not None:
            raise self.restore_error
        self.restores.append(filename)
        return self.restore_result

    def close(self) -> None:
        pass


def _messages(document: str = "d" * 1500, question: str = "问题一") -> list[dict[str, Any]]:
    return [{"role": "user", "content": f"{document}\n问题：{question}"}]


def _wait(predicate, timeout_s: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_document_key_is_document_only_and_stable() -> None:
    first = document_key(_messages(question="A"), min_chars=100)
    second = document_key(_messages(question="B"), min_chars=100)
    assert first == second
    assert first is not None
    assert document_key(_messages(document="short", question="A"), min_chars=100) is None


def test_document_key_ignores_non_user_messages() -> None:
    messages = [
        {"role": "system", "content": "x" * 2000},
        {"role": "user", "content": "doc\n问题：q"},
    ]
    assert document_key(messages, min_chars=100) is None


def test_snapshot_client_restores_existing_file_once(tmp_path: Path) -> None:
    inner = _FakeInner()
    config = SnapshotConfig(enabled=True, directory=tmp_path, min_chars=100)
    client = SnapshotClient(inner, config, "large-test")
    key = document_key(_messages(), min_chars=100)
    assert key is not None
    filename = f"large-test-{key[:32]}.bin"
    (tmp_path / filename).write_bytes(b"x")

    first = client.chat(_messages(question="A"))
    second = client.chat(_messages(question="B"))

    assert first["_jev_snapshot"] == "restore"
    assert second["_jev_snapshot"] == "miss"
    assert inner.restores == [filename]


def test_snapshot_client_async_save_and_lru(tmp_path: Path) -> None:
    inner = _FakeInner()
    inner.save_dir = tmp_path
    config = SnapshotConfig(
        enabled=True, directory=tmp_path, max_entries=1, min_chars=100
    )
    client = SnapshotClient(inner, config, "ns")

    client.chat(_messages(document="a" * 1500, question="A"))
    assert _wait(lambda: len(inner.saves) == 1)
    first_key = document_key(_messages(document="a" * 1500), min_chars=100)
    assert first_key is not None
    first_path = tmp_path / f"ns-{first_key[:32]}.bin"
    assert _wait(first_path.exists)
    first_path.write_bytes(b"x")

    client.chat(_messages(document="b" * 1500, question="B"))
    assert _wait(lambda: len(inner.saves) == 2)
    assert _wait(lambda: not first_path.exists())
    assert len(list(tmp_path.glob("ns-*.bin"))) == 1


def test_snapshot_client_deletes_corrupt_file_and_falls_back(tmp_path: Path) -> None:
    inner = _FakeInner()
    inner.restore_error = JevProtocolError("bad snapshot")
    config = SnapshotConfig(enabled=True, directory=tmp_path, min_chars=100)
    client = SnapshotClient(inner, config, "ns")
    key = document_key(_messages(), min_chars=100)
    assert key is not None
    path = tmp_path / f"ns-{key[:32]}.bin"
    path.write_bytes(b"broken")

    response = client.chat(_messages())

    assert response["_jev_snapshot"] == "error"
    assert not path.exists()
    assert len(inner.chats) == 1


def test_snapshot_client_short_document_has_no_status(tmp_path: Path) -> None:
    inner = _FakeInner()
    config = SnapshotConfig(enabled=True, directory=tmp_path, min_chars=1000)
    client = SnapshotClient(inner, config, "ns")
    response = client.chat(
        [{"role": "user", "content": "短文档\n问题：问"}]
    )
    assert "_jev_snapshot" not in response
    assert inner.saves == []


def test_snapshot_client_stream_bypasses_snapshot(tmp_path: Path) -> None:
    inner = _FakeInner()
    config = SnapshotConfig(enabled=True, directory=tmp_path, min_chars=100)
    client = SnapshotClient(inner, config, "ns")
    chunks = list(client.chat_stream(_messages()))
    assert chunks == [{"choices": [{"delta": {"content": "ok"}}]}]
    assert inner.restores == []
    assert inner.saves == []


def test_snapshot_config_validation() -> None:
    with pytest.raises(ValueError):
        SnapshotConfig(enabled=True, directory=None)
    with pytest.raises(ValueError):
        SnapshotConfig(enabled=True, directory=Path("."), max_entries=0)
    with pytest.raises(ValueError):
        SnapshotConfig(enabled=True, directory=Path("."), slot_id=-1)


def test_build_snapshot_config_default_off_and_parallel_guard(tmp_path: Path) -> None:
    from jev.kernel.kernel import _build_snapshot_config

    assert not _build_snapshot_config({}).enabled
    with pytest.raises(JevProtocolError, match="parallel=1"):
        _build_snapshot_config(
            {
                "cerebrum": {
                    "kv_snapshot_dir": str(tmp_path / "snap"),
                    "parallel": 2,
                }
            }
        )
    enabled = _build_snapshot_config(
        {"cerebrum": {"kv_snapshot_dir": str(tmp_path / "snap")}}
    )
    assert enabled.enabled
    assert enabled.directory is not None and enabled.directory.is_dir()


def test_manager_build_cmd_adds_slot_args(tmp_path: Path) -> None:
    mgr = LlamaServerManager(
        llama_server_bin=Path("llama-server.exe"),
        gguf_path=Path("model.gguf"),
        port=8388,
        extra_args=[],
        slot_save_path=tmp_path,
    )
    cmd = mgr._build_cmd()
    assert "--slots" in cmd
    assert cmd[cmd.index("--slot-save-path") + 1] == str(tmp_path)
    assert cmd[cmd.index("--parallel") + 1] == "1"
