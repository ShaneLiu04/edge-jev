"""T011/T013 IT：真实 llama-server 子进程全链路——生命周期 + 真实生成 + chat 级联编排。

FR3（大脑后端）+ FR1（chat 端点）+ FR5（级联三路径）+ FR8（台账）：
spawn → /health → OpenAI 兼容生成 → PRE/MID/POST 决策（真 laya 批量前向）
→ 级联路由 → 台账落盘 → 优雅退出无残留。
"""

import socket
import urllib.request

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jev.kernel.ledger.query import LedgerQuery
from jev.server.chat import create_chat_router

pytestmark = pytest.mark.assets

CHAT_MESSAGES = [{"role": "user", "content": "用一句话介绍你自己"}]


def _chat_app(real_kernel, brain_client) -> FastAPI:
    app = FastAPI()
    app.include_router(
        create_chat_router(
            real_kernel,
            brain_client,
            real_kernel.registry,
            engine=real_kernel.engine,
            ledger=real_kernel.ledger,
        )
    )
    return app


class TestLlamaServerLifecycle:
    def test_spawn_health_stop_clean(self, own_brain_manager):
        """独享实例：拉起 → 健康 → 优雅退出无残留（不影响共享会话大脑）。"""
        manager = own_brain_manager
        assert manager.is_running()
        with urllib.request.urlopen(f"{manager.base_url}/health", timeout=5) as r:
            assert 200 <= r.status < 300

        manager.stop()
        assert not manager.is_running()
        host, port = manager.base_url.rsplit(":", 1)
        with socket.socket() as sock:
            sock.settimeout(2.0)
            try:
                sock.connect((host, int(port)))
            except OSError:
                return
        pytest.fail("llama-server 退出后端口仍被占用（进程残留）")

    def test_client_health_true(self, brain_client):
        assert brain_client.health() is True


class TestBrainRealGeneration:
    def test_chat_real_generation(self, brain_client):
        response = brain_client.chat(CHAT_MESSAGES, max_tokens=64)
        content = response["choices"][0]["message"]["content"]
        assert isinstance(content, str) and content.strip()
        assert response["usage"]["total_tokens"] > 0


class TestChatEndpointFullChain:
    def test_chat_with_real_cascade_and_ledger(self, real_kernel, brain_client):
        """PRE/MID/POST 真决策（laya 批量前向）+ 真大脑生成 + 级联路由头 + 台账落盘。"""
        client = TestClient(_chat_app(real_kernel, brain_client))
        response = client.post(
            "/v1/chat/completions",
            json={"messages": CHAT_MESSAGES, "max_tokens": 64},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["choices"][0]["message"]["content"].strip()
        assert response.headers["X-Jev-Route"] in {"direct", "light", "full"}
        # active PRE 5 点必评估；light/full 另有 MID/POST 批
        decisions = int(response.headers["X-Jev-Decisions"])
        assert decisions >= 5

        # FR8：该请求在台账按 request_id 聚合为 decision/route/answer 三类条目
        request_id = body["id"].removeprefix("chatcmpl-jev-")
        real_kernel.ledger.flush()
        entries = LedgerQuery(dir="data/ledger").query(request_id)
        kinds = {entry.kind for entry in entries}
        assert kinds == {"decision", "route", "answer"}
        route_entry = next(e for e in entries if e.kind == "route")
        assert route_entry.route == response.headers["X-Jev-Route"]
        answer_entry = next(e for e in entries if e.kind == "answer")
        assert answer_entry.content == body["choices"][0]["message"]["content"]

    def test_chat_state_serialized_into_decision_context(self, real_kernel, brain_client):
        """messages 完整进入决策 state（真实链路：messages JSON → 小脑 state）。"""
        client = TestClient(_chat_app(real_kernel, brain_client))
        response = client.post(
            "/v1/chat/completions",
            json={
                "messages": [
                    {"role": "system", "content": "你是本地助手"},
                    {"role": "user", "content": "把这句话翻译成英文：今天天气很好"},
                ],
                "max_tokens": 64,
            },
        )
        assert response.status_code == 200
        # 决策 state 越长 token 越多——仅断言链路真实执行（active PRE 全量前向）
        assert int(response.headers["X-Jev-Decisions"]) >= 5
        assert response.json()["choices"][0]["message"]["content"].strip()
