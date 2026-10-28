"""AR008 真实异构投机解码集成测试（target GPU + draft CPU，无 mock）。"""

import os
import socket
from pathlib import Path

import pytest

from jev.kernel.cerebrum.llama_server import LlamaServerClient, LlamaServerManager

pytestmark = pytest.mark.assets
REPO_ROOT = Path(__file__).resolve().parents[2]
BIN = REPO_ROOT / "models" / "llama-server" / "bin" / "llama-server.exe"
TARGET = REPO_ROOT / "models" / "qwen" / "qwen2.5-1.5b-instruct-q4_k_m.gguf"
DRAFT = REPO_ROOT / "models" / "qwen" / "qwen2.5-0.5b-instruct-q4_k_m.gguf"
CUDART = REPO_ROOT / "models" / "llama-server" / "cudart"


@pytest.mark.slow
def test_speculative_decoding_draft_on_cpu_reports_acceptance():
    assert DRAFT.is_file(), f"draft 资产缺失: {DRAFT}（jev fetch qwen2.5-0.5b-gguf）"
    if CUDART.is_dir():
        os.environ["PATH"] = str(CUDART) + os.pathsep + os.environ["PATH"]
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    manager = LlamaServerManager(
        llama_server_bin=BIN,
        gguf_path=TARGET,
        port=port,
        ctx_size=2048,
        probe_timeout_s=240.0,
        restart_max=0,
        extra_args=[
            "--n-gpu-layers",
            "99",
            "--spec-draft-model",
            str(DRAFT),
            "--spec-type",
            "draft-simple",
            "--spec-draft-n-max",
            "8",
            "--spec-draft-ngl",
            "0",
            "--spec-draft-threads",
            "8",
            "--spec-draft-sampling",
            "greedy",
        ],
    )
    client = LlamaServerClient(f"http://127.0.0.1:{port}", timeout_s=240.0)
    try:
        manager.start()
        response = client.chat(
            [
                {
                    "role": "user",
                    "content": (
                        "Write a short paragraph about why caching helps "
                        "web performance."
                    ),
                }
            ],
            max_tokens=48,
            temperature=0.0,
        )
        text = response["choices"][0]["message"]["content"].strip()
        timings = response.get("timings") or {}
        assert text
        assert int(timings.get("draft_n", 0)) > 0
        assert 0 <= int(timings.get("draft_n_accepted", 0)) <= int(
            timings.get("draft_n", 0)
        )
        assert float(timings.get("predicted_per_second", 0.0)) > 0
    finally:
        client.close()
        manager.stop()
