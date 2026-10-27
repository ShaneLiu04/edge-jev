"""AR007 真实前缀 KV 复用集成测试（真实 Laya + 真实 llama-server，无 mock）。"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jev.kernel.cerebrum.llama_server import LlamaServerClient
from jev.kernel.runtime.config import RuntimeConfig
from jev.server.chat import create_chat_router

pytestmark = pytest.mark.assets
REPO_ROOT = Path(__file__).resolve().parents[2]


def _app(kernel, brain, runtime: RuntimeConfig) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_chat_router(
            kernel.cerebellum,
            brain,
            kernel.registry,
            engine=kernel.engine,
            runtime_config=runtime,
        )
    )
    return TestClient(app)


@pytest.mark.slow
def test_second_same_prefix_request_hits_server_cache(real_kernel, own_brain_manager):
    brain = LlamaServerClient(
        own_brain_manager.base_url, timeout_s=180.0, cache_prompt=True
    )
    client = _app(
        real_kernel,
        brain,
        RuntimeConfig(enabled=True, prefix_cache=True, session_affinity=True),
    )
    document = "仓库巡检记录。\n\n" + "\n\n".join(
        f"第{i}段：区域 {chr(65 + i % 6)} 温度 {18 + i % 8} 湿度 {40 + i % 20}，"
        f"编号 K-{1000 + i:04d} 的校验码为 {4815 + i * 37}。"
        for i in range(12)
    )
    first_payload = {
        "model": "local",
        "user": "session-real-1",
        "messages": [
            {
                "role": "user",
                "content": f"{document}\n\n问题：编号 K-1000 的校验码是多少？只输出数字。",
            }
        ],
        "temperature": 0.0,
        "max_tokens": 8,
    }
    first = client.post("/v1/chat/completions", json=first_payload)
    assert first.status_code == 200
    assert first.headers.get("X-Jev-Prefix-Cache") in {"miss", "hit:0", None}

    second_payload = {
        **first_payload,
        "messages": [
            {
                "role": "user",
                "content": f"{document}\n\n问题：编号 K-1001 的校验码是多少？只输出数字。",
            }
        ],
    }
    second = client.post("/v1/chat/completions", json=second_payload)
    assert second.status_code == 200
    header = second.headers.get("X-Jev-Prefix-Cache", "")
    assert header.startswith("hit:"), header
    cached = int(header.split(":", 1)[1])
    assert cached > 100
    assert second.json()["choices"][0]["message"]["content"].strip()
    assert second.elapsed.total_seconds() < first.elapsed.total_seconds() * 2.0
