"""AR018 真实双 llama-server 级联：小模型首答、接地验证与大模型升级。"""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest

from jev.bench.prefixset import build_shared_prefix
from jev.kernel.cerebrum.llama_server import LlamaServerClient, LlamaServerManager
from jev.kernel.cerebrum.tiering import TierConfig, TieredBrain

pytestmark = [pytest.mark.assets, pytest.mark.slow]

REPO = Path(__file__).resolve().parents[2]
BIN = REPO / "models" / "llama-server" / "bin" / "llama-server.exe"
LARGE = REPO / "models" / "qwen" / "qwen2.5-1.5b-instruct-q4_k_m.gguf"
SMALL = REPO / "models" / "qwen" / "qwen2.5-0.5b-instruct-q4_k_m.gguf"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _spawn(gguf: Path) -> LlamaServerManager:
    cudart = REPO / "models" / "llama-server" / "cudart"
    if cudart.is_dir():
        os.environ["PATH"] = str(cudart) + os.pathsep + os.environ["PATH"]
    gpu_layers = int(os.environ.get("JEV_TEST_GPU_LAYERS", "0"))
    manager = LlamaServerManager(
        llama_server_bin=BIN,
        gguf_path=gguf,
        port=_free_port(),
        ctx_size=4096,
        probe_timeout_s=180.0,
        restart_max=0,
        extra_args=["--n-gpu-layers", str(gpu_layers)],
    )
    manager.start()
    return manager


def test_tiered_brain_real_small_or_escalates_with_grounding():
    for path in (BIN, LARGE, SMALL):
        if not path.exists():
            pytest.skip(f"缺少真实资产: {path}")
    question = build_shared_prefix(1)[0]
    messages = [{"role": "user", "content": question.prompt}]
    large = _spawn(LARGE)
    small = _spawn(SMALL)
    try:
        brain = TieredBrain(
            LlamaServerClient(large.base_url, timeout_s=180.0),
            LlamaServerClient(small.base_url, timeout_s=180.0),
            TierConfig(
                enabled=True,
                ground_window=600,
                max_answer_chars=64,
                prewarm_large=False,
            ),
        )
        response, tier, reason = brain.chat_full(
            messages,
            max_tokens=16,
            temperature=0.0,
            stop=None,
            timeout_s=180.0,
        )
        content = response["choices"][0]["message"]["content"].strip()
        assert content
        assert tier in {"small", "small->large"}
        assert reason
        assert response["usage"]["completion_tokens"] >= 1
    finally:
        small.stop()
        large.stop()
