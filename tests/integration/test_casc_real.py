"""AR009 真实 CASC 集成测试（真实 embedding + 真实 llama-server，无 mock）。"""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jev.kernel.cerebrum.embeddings import EmbeddingClient, EmbeddingService
from jev.kernel.cerebrum.llama_server import LlamaServerClient, LlamaServerManager
from jev.kernel.prefill.evidence import EvidenceConfig, EvidenceSelector
from jev.kernel.prefill.stable import StableCompressor, StableConfig
from jev.kernel.runtime.config import RuntimeConfig
from jev.server.chat import create_chat_router

pytestmark = pytest.mark.assets
REPO_ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _spawn_embedding() -> EmbeddingService:
    cudart = REPO_ROOT / "models" / "llama-server" / "cudart"
    os.environ["PATH"] = str(cudart) + os.pathsep + os.environ["PATH"]
    gguf = REPO_ROOT / "models" / "qwen" / "qwen2.5-1.5b-instruct-q4_k_m.gguf"
    manager = LlamaServerManager(
        llama_server_bin=REPO_ROOT / "models" / "llama-server" / "bin" / "llama-server.exe",
        gguf_path=gguf,
        port=_free_port(),
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
            "0",
        ],
    )
    manager.start()
    service = EmbeddingService(
        manager,
        EmbeddingClient(f"{manager.base_url}", timeout_s=25.0),
    )
    if not service.available():
        service.close()
        raise RuntimeError("真实 embedding 服务启动失败")
    return service


@pytest.mark.slow
def test_casc_compresses_once_and_reuses_prefix(real_kernel, own_brain_manager):
    service = _spawn_embedding()
    try:
        selector = EvidenceSelector(service.embed, EvidenceConfig())
        compressor = StableCompressor(
            selector.embed_texts,
            StableConfig(
                enabled=True,
                ratio=0.6,
                block_tokens=128,
                min_tokens=512,
                cache_entries=8,
            ),
        )
        runtime = RuntimeConfig(
            enabled=True,
            prefix_cache=True,
            session_affinity=True,
            stable=compressor.config,
        )
        brain = LlamaServerClient(
            own_brain_manager.base_url, timeout_s=180.0, cache_prompt=True
        )
        app = FastAPI()
        app.include_router(
            create_chat_router(
                real_kernel.cerebellum,
                brain,
                real_kernel.registry,
                engine=real_kernel.engine,
                runtime_config=runtime,
                embeddings=service,
                evidence=selector,
                stable_compressor=compressor,
            )
        )
        client = TestClient(app)
        document = "仓库巡检记录。\n\n" + "\n\n".join(
            f"第{index}段：区域 {chr(65 + index % 6)} 温度 {18 + index % 8} "
            f"湿度 {40 + index % 20}，编号 K-{1000 + index:04d} 的校验码是"
            f"{4815 + index * 37}，该记录仅出现一次。"
            for index in range(40)
        )
        payload = {
            "model": "local",
            "user": "casc-real-session",
            "temperature": 0.0,
            "max_tokens": 8,
        }
        first = client.post(
            "/v1/chat/completions",
            json={
                **payload,
                "messages": [
                    {
                        "role": "user",
                        "content": f"{document}\n\n问题：K-1003 的校验码是多少？只输出数字。",
                    }
                ],
            },
        )
        assert first.status_code == 200
        assert first.headers.get("X-Jev-Stable-Compression", "")
        assert first.headers.get("X-Jev-Stable-Cache") in {"miss", "hit"}

        second = client.post(
            "/v1/chat/completions",
            json={
                **payload,
                "messages": [
                    {
                        "role": "user",
                        "content": f"{document}\n\n问题：K-1021 的校验码是多少？只输出数字。",
                    }
                ],
            },
        )
        assert second.status_code == 200
        assert second.headers.get("X-Jev-Stable-Cache") == "hit"
        prefix_header = second.headers.get("X-Jev-Prefix-Cache", "")
        assert prefix_header.startswith("hit:"), prefix_header
        assert int(prefix_header.split(":", 1)[1]) > 100
        assert "5592" in second.json()["choices"][0]["message"]["content"]
    finally:
        service.close()
