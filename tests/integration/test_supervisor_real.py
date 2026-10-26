"""AR006 真实后端集成：大脑自愈 + 真实 embedding 证据选择（无 mock）。"""

import os
import socket
from pathlib import Path

import pytest

from jev.bench.needles import build_needles
from jev.kernel.cerebrum.embeddings import EmbeddingClient, EmbeddingService
from jev.kernel.cerebrum.llama_server import LlamaServerManager
from jev.kernel.cerebrum.supervisor import BrainSupervisor
from jev.kernel.prefill.blocks import split_blocks
from jev.kernel.prefill.evidence import EvidenceSelector
from jev.kernel.prefill.planner import PrefillConfig, plan_prefill

pytestmark = pytest.mark.assets

REPO_ROOT = Path(__file__).resolve().parents[2]
LLAMA_BIN = REPO_ROOT / "models" / "llama-server" / "bin" / "llama-server.exe"
GGUF = REPO_ROOT / "models" / "qwen" / "qwen2.5-1.5b-instruct-q4_k_m.gguf"
CUDART = REPO_ROOT / "models" / "llama-server" / "cudart"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _prepare_env() -> None:
    if CUDART.is_dir():
        os.environ["PATH"] = str(CUDART) + os.pathsep + os.environ["PATH"]


@pytest.mark.slow
def test_supervisor_restarts_crashed_brain_and_completes():
    _prepare_env()
    manager = LlamaServerManager(
        llama_server_bin=LLAMA_BIN,
        gguf_path=GGUF,
        port=_free_port(),
        ctx_size=512,
        probe_timeout_s=180.0,
        restart_max=0,
        extra_args=["--n-gpu-layers", "99"],
    )
    supervisor = BrainSupervisor(
        manager=manager,
        health_interval_s=0.0,
        health_timeout_s=2.0,
        max_recoveries=3,
        recovery_cooldown_s=0.0,
    )
    try:
        manager.start()
        assert supervisor.health() is True
        first = supervisor.chat(
            [{"role": "user", "content": "只回答：好"}], max_tokens=4
        )
        assert first["choices"][0]["message"]["content"].strip()

        manager.stop()
        assert manager.is_running() is False
        second = supervisor.chat(
            [{"role": "user", "content": "只回答：好"}], max_tokens=4
        )
        assert second["choices"][0]["message"]["content"].strip()
        stats = supervisor.stats()
        assert stats["restarts"] >= 1
        assert stats["retries"] >= 0
        assert manager.is_running() is True
    finally:
        supervisor.close()
        manager.stop()


@pytest.mark.slow
def test_evidence_selector_with_real_embedding_server():
    _prepare_env()
    port = _free_port()
    manager = LlamaServerManager(
        llama_server_bin=LLAMA_BIN,
        gguf_path=GGUF,
        port=port,
        ctx_size=4096,
        probe_timeout_s=180.0,
        restart_max=0,
        extra_args=[
            "--embeddings",
            "--pooling",
            "mean",
            "--batch-size",
            "2048",
            "--ubatch-size",
            "1024",
            "--n-gpu-layers",
            "99",
        ],
    )
    client = EmbeddingClient(f"http://127.0.0.1:{port}", timeout_s=25.0)
    service = EmbeddingService(manager, client)
    try:
        assert service.available()
        question = build_needles(1)[0]
        messages = [{"role": "user", "content": question.prompt}]
        blocks = split_blocks(messages, block_tokens=64)
        selector = EvidenceSelector(service.embed)
        result = selector.score(blocks, question.prompt, {})
        assert result.usable
        assert len(result.scores) == len(blocks)
        assert result.vectors and len(result.vectors[0]) > 64

        config = PrefillConfig(
            enabled=True,
            evidence=True,
            adaptive=True,
            min_blocks=4,
            block_tokens=64,
        )
        plan = plan_prefill(messages, question.prompt, {}, config, evidence=selector)
        assert plan.selector == "evidence"
        assert plan.skipped is None
        assert plan.after_tokens < plan.before_tokens
        assert 0.35 <= plan.budget_ratio <= 0.75
    finally:
        service.close()
