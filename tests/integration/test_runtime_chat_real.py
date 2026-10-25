"""AR003 T013：运行时能力真实后端集成（真 Laya + 真 llama-server，无 mock）。

覆盖：三带头字段、验证器真实调用、精确命中短路、prefill 跳过信号、
独立 embedding 实例真实向量维度。
"""

import os
import socket
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jev.kernel.cache.semantic import CacheConfig, SemanticCache, cosine
from jev.kernel.cerebrum.embeddings import EmbeddingClient, EmbeddingService
from jev.kernel.cerebrum.llama_server import LlamaServerClient, LlamaServerManager
from jev.kernel.runtime.bands import BandPolicy
from jev.kernel.runtime.config import RuntimeConfig
from jev.kernel.runtime.prompt import PromptConfig
from jev.kernel.runtime.verifier import BrainVerifier
from jev.server.chat import create_chat_router

pytestmark = pytest.mark.assets

REPO_ROOT = Path(__file__).resolve().parents[2]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]

REPO_ROOT = Path(__file__).resolve().parents[2]


def _build_app(kernel, brain: LlamaServerClient, runtime: RuntimeConfig, cache):
    app = FastAPI()
    app.include_router(
        create_chat_router(
            kernel.cerebellum,
            brain,
            kernel.registry,
            engine=kernel.engine,
            runtime_config=runtime,
            cache=cache,
        )
    )
    return TestClient(app)


def _runtime(**overrides) -> RuntimeConfig:
    defaults = dict(
        enabled=True,
        bands=BandPolicy(verifier_enabled=False),
        cache=CacheConfig(enabled=True),
        prompt=PromptConfig(),
    )
    defaults.update(overrides)
    return RuntimeConfig(**defaults)


def test_runtime_headers_and_exact_cache(real_kernel, brain_client):
    cache = SemanticCache(CacheConfig(enabled=True))
    client = _build_app(real_kernel, brain_client, _runtime(), cache)
    payload = {"model": "local", "messages": [{"role": "user", "content": "你好"}]}

    first = client.post("/v1/chat/completions", json=payload)
    assert first.status_code == 200
    headers = first.headers
    assert 0.0 <= float(headers["X-Jev-U"]) <= 1.0
    assert headers["X-Jev-Verified"] == "off"
    assert headers["X-Jev-Prefill"].startswith(("skipped:", "off"))
    route = headers["X-Jev-Route"]
    assert route in {"direct", "light", "mid", "full"}

    if route not in {"direct", "light"}:
        pytest.skip(f"真实小脑判为 {route}，不可缓存（非缺陷）")

    second = client.post("/v1/chat/completions", json=payload)
    assert second.status_code == 200
    assert second.headers["X-Jev-Cache"] == "hit-exact"
    assert second.json()["choices"][0]["message"]["content"] == (
        first.json()["choices"][0]["message"]["content"]
    )
    assert second.json()["usage"]["total_tokens"] == 0
    stats = cache.stats()
    assert stats["hits_exact"] == 1
    assert stats["stores"] >= 1


def test_verifier_real_brain_accept_or_reject(brain_client):
    verifier = BrainVerifier(brain_client, max_tokens=8)
    result = verifier.verify("1+1 等于几？", "等于 2", {"intent": "simple"})
    assert result.verdict in {"accept", "reject"}
    assert result.unavailable is False
    assert 0 <= result.tokens <= 8
    assert result.latency_ms > 0


def test_verifier_timeout_is_fail_safe(brain_client):
    verifier = BrainVerifier(brain_client, max_tokens=8)
    result = verifier.verify("解释黎曼猜想", "略", {}, timeout_s=0.001)
    assert result.unavailable is True
    assert result.verdict == "reject"


@pytest.mark.slow
def test_real_embedding_server_dimensions():
    cudart_dir = REPO_ROOT / "models" / "llama-server" / "cudart"
    os.environ["PATH"] = str(cudart_dir) + os.pathsep + os.environ["PATH"]
    port = free_port()
    manager = LlamaServerManager(
        llama_server_bin=REPO_ROOT / "models" / "llama-server" / "bin" / "llama-server.exe",
        gguf_path=REPO_ROOT / "models" / "qwen" / "qwen2.5-1.5b-instruct-q4_k_m.gguf",
        port=port,
        ctx_size=512,
        probe_timeout_s=120.0,
        restart_max=1,
        extra_args=["--embeddings", "--pooling", "mean"],
    )
    client = EmbeddingClient(f"http://127.0.0.1:{port}", timeout_s=30.0)
    service = EmbeddingService(manager, client)
    try:
        vectors = service.embed(["苹果是一种水果", "苹果是一种水果", "量子色动力学"])
        assert len(vectors) == 3
        dimension = len(vectors[0])
        assert dimension > 64
        assert all(len(vector) == dimension for vector in vectors)
        left, same, other = (cosine(vectors[0], v) for v in vectors)
        assert same == pytest.approx(1.0, abs=1e-4)
        assert left > other

        cache = SemanticCache(CacheConfig(enabled=True, similarity=0.9, margin=0.0))
        cache.store("fp", "苹果是一种水果", "苹果是水果。", "light", embeddings=service)
        hit = cache.lookup("fp", "苹果是一种水果。", embeddings=service)
        assert hit is not None and hit.layer == "semantic"
        miss = cache.lookup("fp", "量子色动力学是什么", embeddings=service)
        assert miss is None
    finally:
        service.close()
