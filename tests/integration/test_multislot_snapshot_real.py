"""AR023 真实 llama-server：MSPP 把同一快照恢复到两个真实槽位。"""

from __future__ import annotations

import os
import socket
import time
from pathlib import Path

import pytest

from jev.bench.prefixset import build_shared_prefix
from jev.kernel.cerebrum.llama_server import LlamaServerClient, LlamaServerManager
from jev.kernel.cerebrum.scheduling import (
    ScheduledClient,
    SlotScheduler,
    SlotSchedulerConfig,
)
from jev.kernel.cerebrum.snapshot import SnapshotClient, SnapshotConfig

pytestmark = [pytest.mark.assets, pytest.mark.slow]

REPO = Path(__file__).resolve().parents[2]
BIN = REPO / "models" / "llama-server" / "bin" / "llama-server.exe"
LARGE = REPO / "models" / "qwen" / "qwen2.5-1.5b-instruct-q4_k_m.gguf"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _manager(snapshot_dir: Path) -> LlamaServerManager:
    return LlamaServerManager(
        llama_server_bin=BIN,
        gguf_path=LARGE,
        port=_free_port(),
        ctx_size=8192,
        probe_timeout_s=180.0,
        restart_max=0,
        extra_args=[
            "--n-gpu-layers",
            os.environ.get("JEV_TEST_GPU_LAYERS", "0"),
            "--parallel",
            "2",
        ],
        slot_save_path=snapshot_dir,
    )


def _client(manager: LlamaServerManager, snapshot_dir: Path) -> ScheduledClient:
    inner = LlamaServerClient(manager.base_url, timeout_s=180.0, cache_prompt=True)
    snap = SnapshotClient(
        inner,
        SnapshotConfig(
            enabled=True,
            directory=snapshot_dir,
            min_chars=200,
            per_request_slot=True,
        ),
        "ar023-real",
    )
    return ScheduledClient(
        snap, SlotScheduler(SlotSchedulerConfig(enabled=True, slots=2))
    )


def test_multislot_snapshot_restores_two_real_slots(tmp_path: Path) -> None:
    for path in (BIN, LARGE):
        if not path.exists():
            pytest.skip(f"缺少真实资产: {path}")
    cudart = REPO / "models" / "llama-server" / "cudart"
    if cudart.is_dir():
        os.environ["PATH"] = str(cudart) + os.pathsep + os.environ["PATH"]
    question = build_shared_prefix(1)[0]
    messages = [{"role": "user", "content": question.prompt}]

    manager = _manager(tmp_path)
    manager.start()
    try:
        client = _client(manager, tmp_path)
        warm = client.chat(messages, max_tokens=8, temperature=0.0)
        assert warm["_jev_snapshot"] in {"save", "miss"}
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline and not any(tmp_path.glob("*.bin")):
            time.sleep(0.05)
        assert list(tmp_path.glob("*.bin"))
    finally:
        manager.stop()

    manager = _manager(tmp_path)
    manager.start()
    try:
        client = _client(manager, tmp_path)
        first = client.chat(messages, max_tokens=8, temperature=0.0)
        second = client.chat(messages, max_tokens=8, temperature=0.0)
        assert first["_jev_snapshot"] == "restore"
        assert second["_jev_snapshot"] == "restore"
        assert first["usage"]["total_tokens"] > 0
        assert second["usage"]["total_tokens"] > 0
    finally:
        manager.stop()
