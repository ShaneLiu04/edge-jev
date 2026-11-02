"""AR022 真实 llama-server：ScheduledClient 的 id_slot 确实落到不同槽位。"""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest

from jev.bench.prefixset import build_shared_prefix
from jev.kernel.cerebrum.llama_server import LlamaServerClient, LlamaServerManager
from jev.kernel.cerebrum.scheduling import (
    ScheduledClient,
    SlotScheduler,
    SlotSchedulerConfig,
)

pytestmark = [pytest.mark.assets, pytest.mark.slow]

REPO = Path(__file__).resolve().parents[2]
BIN = REPO / "models" / "llama-server" / "bin" / "llama-server.exe"
LARGE = REPO / "models" / "qwen" / "qwen2.5-1.5b-instruct-q4_k_m.gguf"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_scheduler_uses_two_real_slots() -> None:
    for path in (BIN, LARGE):
        if not path.exists():
            pytest.skip(f"缺少真实资产: {path}")
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
            "--parallel",
            "2",
        ],
    )
    manager.start()
    try:
        inner = LlamaServerClient(manager.base_url, timeout_s=180.0)
        client = ScheduledClient(
            inner,
            SlotScheduler(SlotSchedulerConfig(enabled=True, slots=2)),
        )
        question = build_shared_prefix(1)[0]
        messages = [{"role": "user", "content": question.prompt}]
        client.chat(messages, max_tokens=8, temperature=0.0, timeout_s=180.0)
        client.chat(messages, max_tokens=8, temperature=0.0, timeout_s=180.0)
        slots = inner.slots()
        used = [
            slot
            for slot in slots
            if int(slot.get("n_prompt_tokens", 0)) > 0
        ]
        assert len(used) == 2
    finally:
        manager.stop()
