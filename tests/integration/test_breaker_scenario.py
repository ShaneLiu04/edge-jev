"""T011 IT-3：熔断场景端到端（FR6）——真连接拒绝 ×3 → OPEN → 快速 503（<100ms）；
小脑熔断期间 chat 端到端不失败（PRE 全量 fallback → 级联安全侧 full → 真大脑生成）。
"""

import socket
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jev.kernel.cascade.breaker import Breaker
from jev.kernel.cerebellum.base import ChoiceQuestion
from jev.kernel.cerebellum.kev_http import KevHttpBackend
from jev.kernel.cerebellum.wire import encode_request
from jev.kernel.kernel import Kernel
from jev.server.app import build_systemone_app
from jev.server.chat import create_chat_router

pytestmark = pytest.mark.assets

STATE = "用户希望在内网环境离线部署一个本地推理服务"
PAYLOAD = encode_request(
    "local",
    STATE,
    {"q": ChoiceQuestion(choices={"a": None, "b": None}, instructions="pick one")},
)
CHAT_MESSAGES = [{"role": "user", "content": "用一句话介绍你自己"}]


def _dead_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture()
def dead_kernel(real_kernel) -> Kernel:
    """同一决策注册表/引擎，但小脑指向已释放端口（真实连接拒绝）。"""
    return Kernel(
        cerebellum=KevHttpBackend(serve_url=f"http://127.0.0.1:{_dead_port()}"),
        registry=real_kernel.registry,
        breaker=Breaker(failure_threshold=3, window_s=60.0, cooldown_s=30.0),
    )


def _chat_app(kernel: Kernel, brain_client) -> FastAPI:
    app = FastAPI()
    engine = kernel.engine
    app.include_router(
        create_chat_router(
            kernel,
            brain_client,
            kernel.registry,
            route_policy=lambda decisions: engine.route_chat(decisions).wire,
        )
    )
    return app


class TestBreakerSystemone:
    def test_503_fast_after_three_real_failures(self, dead_kernel):
        client = TestClient(build_systemone_app(dead_kernel))
        for _ in range(3):
            response = client.post("/v1/systemone", json=PAYLOAD)
            assert response.status_code == 503
        assert dead_kernel.breaker.state.value == "open"

        start = time.perf_counter()
        response = client.post("/v1/systemone", json=PAYLOAD)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert response.status_code == 503
        assert response.headers["Retry-After"] == "1"
        assert elapsed_ms < 100, f"熔断期 503 耗时 {elapsed_ms:.1f}ms（FR6 要求 <100ms）"


class TestBreakerChatResilience:
    def test_chat_never_fails_on_cerebellum_outage(self, dead_kernel, brain_client):
        """设计 §4.2 异常流程 1：端到端请求永不因小脑失败而失败。

        小脑死端口（真连接拒绝）→ PRE 全量 fallback（from_fallback=True）
        → 级联安全侧 full → 真大脑生成 200。
        """
        chat_client = TestClient(_chat_app(dead_kernel, brain_client))
        systemone_client = TestClient(build_systemone_app(dead_kernel))
        # 先打挂小脑（3 次真失败 → 熔断 OPEN；共享同一 dead_kernel 熔断器）
        for _ in range(3):
            response = systemone_client.post("/v1/systemone", json=PAYLOAD)
            assert response.status_code == 503
        assert dead_kernel.breaker.state.value == "open"

        response = chat_client.post(
            "/v1/chat/completions",
            json={"messages": CHAT_MESSAGES, "max_tokens": 64},
        )
        assert response.status_code == 200
        assert response.json()["choices"][0]["message"]["content"].strip()
        # 小脑全量 fallback → 级联安全侧直上大脑
        assert response.headers["X-Jev-Route"] == "full"
