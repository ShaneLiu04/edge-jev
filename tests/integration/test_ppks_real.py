"""AR019 真实 llama-server 冷重启：slot KV 快照保存/恢复后可复用文档前缀。"""

from __future__ import annotations

import os
import socket
import time
from pathlib import Path

import pytest

from jev.bench.prefixset import build_shared_prefix
from jev.kernel.cerebrum.llama_server import LlamaServerClient, LlamaServerManager
from jev.kernel.cerebrum.snapshot import (
    SnapshotClient,
    SnapshotConfig,
    document_key,
)

pytestmark = [pytest.mark.assets, pytest.mark.slow]

REPO = Path(__file__).resolve().parents[2]
BIN = REPO / "models" / "llama-server" / "bin" / "llama-server.exe"
LARGE = REPO / "models" / "qwen" / "qwen2.5-1.5b-instruct-q4_k_m.gguf"
NAMESPACE = "it-ppks"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _spawn(slot_dir: Path) -> LlamaServerManager:
    cudart = REPO / "models" / "llama-server" / "cudart"
    if cudart.is_dir():
        os.environ["PATH"] = str(cudart) + os.pathsep + os.environ["PATH"]
    manager = LlamaServerManager(
        llama_server_bin=BIN,
        gguf_path=LARGE,
        port=_free_port(),
        ctx_size=8192,
        probe_timeout_s=180.0,
        restart_max=0,
        extra_args=[
            "--n-gpu-layers",
            os.environ.get("JEV_TEST_GPU_LAYERS", "99"),
        ],
        slot_save_path=slot_dir,
    )
    manager.start()
    return manager


def _client(manager: LlamaServerManager, config: SnapshotConfig) -> SnapshotClient:
    return SnapshotClient(
        LlamaServerClient(manager.base_url, timeout_s=300.0, cache_prompt=True),
        config,
        NAMESPACE,
    )


def _wait_file(path: Path, timeout_s: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if path.exists() and path.stat().st_size > 0:
            return
        time.sleep(0.1)
    raise AssertionError(f"快照未生成: {path}")


def test_ppks_real_restore_reuses_document_prefix(tmp_path: Path) -> None:
    for path in (BIN, LARGE):
        if not path.exists():
            pytest.skip(f"缺少真实资产: {path}")
    questions = build_shared_prefix(2)
    config = SnapshotConfig(
        enabled=True, directory=tmp_path, max_entries=2, min_chars=1000
    )
    key = document_key(
        [{"role": "user", "content": questions[1].prompt}], min_chars=1000
    )
    assert key is not None
    snapshot_path = tmp_path / f"{NAMESPACE}-{key[:32]}.bin"

    manager = _spawn(tmp_path)
    try:
        response = _client(manager, config).chat(
            [{"role": "user", "content": questions[0].prompt}],
            max_tokens=16,
            temperature=0.0,
            timeout_s=300.0,
        )
        assert response["_jev_snapshot"] == "save"
        _wait_file(snapshot_path)
    finally:
        manager.stop()

    manager = _spawn(tmp_path)
    try:
        response = _client(manager, config).chat(
            [{"role": "user", "content": questions[1].prompt}],
            max_tokens=16,
            temperature=0.0,
            timeout_s=300.0,
        )
        timings = response.get("timings") or {}
        assert response["_jev_snapshot"] == "restore"
        assert int(timings.get("cache_n", 0)) > 1000
        assert int(timings.get("prompt_n", 0)) < 800
        content = response["choices"][0]["message"]["content"]
        assert questions[1].gold.value in content
    finally:
        manager.stop()
