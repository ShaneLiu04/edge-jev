"""T007：/v1/chat/completions 端点测试——真实 FastAPI TestClient + 真实 LayaOnnxBackend
+ 真实 OpenAI 兼容 http.server 大脑对端。

约束 C1：禁 mock 后端。小脑真实契约微型 bundle 由 tests/unit/conftest.py 提供
（onnxruntime 真实加载推理）；大脑为标准库 http.server 实现的真实协议对端
（test_llama_client.py 的 _RecordingServer 模式），端点经 LlamaServerClient 打真实
HTTP。测试只断言 HTTP 行为（状态码/响应体/头）与大脑侧真实收到的请求体。
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jev.kernel.cerebellum.laya_onnx import LayaOnnxBackend
from jev.kernel.cerebrum.llama_server import LlamaServerClient
from jev.kernel.decisions.builtin import load_builtin_registry
from jev.kernel.decisions.framework import Decision, Stage
from jev.kernel.decisions.probes import plan_questions
from jev.server.chat import create_chat_router

HANG_SECONDS = 0.8

MESSAGES = [
    {"role": "system", "content": "你是本地大脑"},
    {"role": "user", "content": "用一句话介绍自己"},
]
VALID_MESSAGE = {"role": "user", "content": "用一句话介绍自己"}

# 服务器侧的真实 OpenAI /v1/chat/completions 响应（原样透传契约的对照基准）
OPENAI_RESPONSE = {
    "id": "chatcmpl-local-1",
    "object": "chat.completion",
    "model": "local",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "本地大脑应答"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
}


@pytest.fixture()
def cerebellum(model_path, tokenizer_path, config_path):
    return LayaOnnxBackend(
        model_path=model_path, tokenizer_path=tokenizer_path, config_path=config_path
    )


class _CountingLayaBackend(LayaOnnxBackend):
    """真实推理的计数子类（super() 全真前向，非 mock）：锁定“单次前向多问”。"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.system_one_calls = 0
        self.last_question_count = 0

    def system_one(self, state, questions):
        self.system_one_calls += 1
        self.last_question_count = len(questions)
        return super().system_one(state, questions)


# ---------------------------------------------------------------------------
# 真实大脑：OpenAI 兼容 http.server 协议对端（_RecordingServer 模式）
# ---------------------------------------------------------------------------


class _OpenAIHandler(BaseHTTPRequestHandler):
    """真实处理 HTTP 请求的协议对端；行为由 server.mode 驱动。"""

    def log_message(self, format, *args):
        pass

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            # 客户端已断开，对端写失败属预期
            pass

    def do_POST(self):
        server = self.server
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = None
        server.requests.append({"path": self.path, "body": body})
        if self.path != "/v1/chat/completions":
            self._send_json(404, {"error": "not found"})
        elif server.mode == "http500":
            self._send_json(500, {"error": "llama-server internal error"})
        elif server.mode == "hang":
            time.sleep(HANG_SECONDS)
            self._send_json(200, OPENAI_RESPONSE)
        else:
            self._send_json(200, OPENAI_RESPONSE)


class _RecordingServer(ThreadingHTTPServer):
    """记录请求的服务器；mode 切换协议对端行为。"""

    daemon_threads = True

    def __init__(self, address) -> None:
        super().__init__(address, _OpenAIHandler)
        self.mode = "ok"
        self.requests: list[dict] = []


@pytest.fixture()
def brain_server():
    server = _RecordingServer(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture()
def brain(brain_server):
    return LlamaServerClient(base_url=f"http://127.0.0.1:{brain_server.server_address[1]}")


@pytest.fixture()
def registry():
    return load_builtin_registry()


# ---------------------------------------------------------------------------
# 应用装配与请求构造
# ---------------------------------------------------------------------------


def _make_client(cerebellum, brain, registry, **router_kwargs) -> TestClient:
    """按实现契约装配被测应用：FastAPI().include_router(create_chat_router(...))。"""
    app = FastAPI()
    app.include_router(create_chat_router(cerebellum, brain, registry, **router_kwargs))
    return TestClient(app)


@pytest.fixture()
def client(cerebellum, brain, registry):
    return _make_client(cerebellum, brain, registry)


def _chat_body(**overrides) -> dict:
    """最小合法请求体，字段级覆盖供错误矩阵用例构造非法值。"""
    body: dict = {"messages": [dict(VALID_MESSAGE)]}
    body.update(overrides)
    return body


def _assert_openai_error(resp, status: int) -> dict:
    """断言 OpenAI 形状错误体 {error:{...}} 且 message 非空，返回 error dict。"""
    assert resp.status_code == status
    body = resp.json()
    assert set(body) == {"error"}
    error = body["error"]
    assert isinstance(error["message"], str) and error["message"]
    return error


def _assert_invalid_request(resp, param: str | None = None) -> None:
    """400 invalid_request_error；给定 param 时断言字段级定位。"""
    error = _assert_openai_error(resp, 400)
    assert error["type"] == "invalid_request_error"
    if param is not None:
        assert error["param"] == param


# ---------------------------------------------------------------------------
# 200：合法请求透传大脑 + 决策头
# ---------------------------------------------------------------------------


def test_valid_request_returns_200_with_openai_body_and_transparent_brain_payload(
    client, brain_server
):
    resp = client.post(
        "/v1/chat/completions",
        json=_chat_body(
            model="gpt-4", temperature=0.2, max_tokens=64, stop=["END"], messages=MESSAGES
        ),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "chatcmpl-local-1"
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["content"] == "本地大脑应答"
    assert body["usage"] == {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}
    # 大脑返回的 OpenAI dict 原样透传
    assert body == OPENAI_RESPONSE

    # 大脑侧真实收到的请求体：messages 原样、参数透传、model 忽略请求值恒为 local
    assert len(brain_server.requests) == 1
    sent = brain_server.requests[0]["body"]
    assert sent["model"] == "local"
    assert sent["messages"] == MESSAGES
    assert sent["temperature"] == 0.2
    assert sent["max_tokens"] == 64
    assert sent["stop"] == ["END"]


def test_default_parameters_are_forwarded_to_brain(client, brain_server):
    resp = client.post("/v1/chat/completions", json=_chat_body())
    assert resp.status_code == 200

    sent = brain_server.requests[0]["body"]
    assert sent["temperature"] == 0.7
    assert sent["max_tokens"] == 512
    assert "stop" not in sent


def test_headers_report_default_route_and_active_pre_decision_count(client, registry):
    # 内置注册表 active PRE 共 5 点：needs_generation/needs_reasoning/intent/complexity/safety
    expected = str(len(registry.active(Stage.PRE)))
    assert expected == "5"

    resp = client.post("/v1/chat/completions", json=_chat_body())
    assert resp.status_code == 200
    assert resp.headers["x-jev-route"] == "full"
    assert resp.headers["x-jev-decisions"] == expected


def test_route_policy_overrides_route_header_without_changing_behavior(
    cerebellum, brain, brain_server, registry
):
    seen: list[list[Decision]] = []

    def route_policy(decisions: list[Decision]) -> str:
        seen.append(decisions)
        return "direct"

    client = _make_client(cerebellum, brain, registry, route_policy=route_policy)
    resp = client.post("/v1/chat/completions", json=_chat_body())
    assert resp.status_code == 200
    assert resp.headers["x-jev-route"] == "direct"
    assert resp.headers["x-jev-decisions"] == "5"

    # route_policy 收到全部 active PRE 决策；大脑仍被真实调用（行为不变）
    assert len(seen) == 1
    assert all(isinstance(d, Decision) for d in seen[0])
    assert [d.point_id for d in seen[0]] == [p.id for p in registry.active(Stage.PRE)]
    assert len(brain_server.requests) == 1


def test_pre_decisions_evaluated_via_single_batched_system_one_call(
    model_path, tokenizer_path, config_path, brain, registry
):
    counting = _CountingLayaBackend(
        model_path=model_path, tokenizer_path=tokenizer_path, config_path=config_path
    )
    client = _make_client(counting, brain, registry)
    resp = client.post("/v1/chat/completions", json=_chat_body())
    assert resp.status_code == 200

    # 单次前向多问：全部 active PRE 点合并为一次 system_one 批量调用
    assert counting.system_one_calls == 1
    planned = len(plan_questions(registry.active(Stage.PRE))[0])
    assert counting.last_question_count == planned


def test_none_cerebellum_falls_back_to_safe_side_but_still_200(brain, brain_server, registry):
    client = _make_client(None, brain, registry)
    resp = client.post("/v1/chat/completions", json=_chat_body())
    assert resp.status_code == 200
    # 决策全走安全侧回退，仍计入 active PRE 决策数
    assert resp.headers["x-jev-decisions"] == "5"
    assert resp.headers["x-jev-route"] == "full"
    assert len(brain_server.requests) == 1


def test_none_registry_yields_zero_decisions(cerebellum, brain):
    client = _make_client(cerebellum, brain, None)
    resp = client.post("/v1/chat/completions", json=_chat_body())
    assert resp.status_code == 200
    assert resp.headers["x-jev-decisions"] == "0"
    assert resp.headers["x-jev-route"] == "full"


# ---------------------------------------------------------------------------
# 503：大脑不可用
# ---------------------------------------------------------------------------


def test_brain_http_500_maps_to_503_with_retry_after(cerebellum, brain, brain_server, registry):
    brain_server.mode = "http500"
    client = _make_client(cerebellum, brain, registry)
    resp = client.post("/v1/chat/completions", json=_chat_body())
    assert resp.status_code == 503
    assert "retry-after" in resp.headers
    _assert_openai_error(resp, 503)


def test_none_brain_returns_503_with_retry_after(cerebellum, registry):
    client = _make_client(cerebellum, None, registry)
    resp = client.post("/v1/chat/completions", json=_chat_body())
    assert resp.status_code == 503
    assert "retry-after" in resp.headers
    _assert_openai_error(resp, 503)


# ---------------------------------------------------------------------------
# 错误矩阵：400 + OpenAI 错误体 + 字段级 param 定位
# ---------------------------------------------------------------------------


def test_non_object_body_returns_400(client):
    resp = client.post("/v1/chat/completions", json=["not", "an", "object"])
    _assert_invalid_request(resp)


def test_missing_messages_returns_400_with_param_location(client):
    resp = client.post("/v1/chat/completions", json={})
    _assert_invalid_request(resp, "messages")


def test_non_list_messages_returns_400_with_param_location(client):
    resp = client.post("/v1/chat/completions", json=_chat_body(messages="你好"))
    _assert_invalid_request(resp, "messages")


def test_empty_messages_returns_400_with_param_location(client):
    resp = client.post("/v1/chat/completions", json=_chat_body(messages=[]))
    _assert_invalid_request(resp, "messages")


def test_non_dict_message_element_returns_400_with_param_location(client):
    resp = client.post("/v1/chat/completions", json=_chat_body(messages=["你好"]))
    _assert_invalid_request(resp, "messages[0]")


def test_missing_role_returns_400_with_param_location(client):
    resp = client.post(
        "/v1/chat/completions", json=_chat_body(messages=[{"content": "你好"}])
    )
    _assert_invalid_request(resp, "messages[0].role")


def test_invalid_role_returns_400_with_param_location_and_openai_error_shape(client):
    resp = client.post(
        "/v1/chat/completions",
        json=_chat_body(messages=[{"role": "wizard", "content": "你好"}]),
    )
    assert resp.status_code == 400
    error = resp.json()["error"]
    # 错误体统一 OpenAI 形状 {error:{message,type,param,code}}
    assert set(error) == {"message", "type", "param", "code"}
    assert error["type"] == "invalid_request_error"
    assert error["param"] == "messages[0].role"
    assert isinstance(error["message"], str) and error["message"]


def test_missing_content_returns_400_with_param_location(client):
    resp = client.post(
        "/v1/chat/completions", json=_chat_body(messages=[{"role": "user"}])
    )
    _assert_invalid_request(resp, "messages[0].content")


def test_non_string_content_returns_400_with_param_location(client):
    resp = client.post(
        "/v1/chat/completions",
        json=_chat_body(messages=[{"role": "user", "content": 42}]),
    )
    _assert_invalid_request(resp, "messages[0].content")


def test_non_number_temperature_returns_400_with_param_location(client):
    resp = client.post("/v1/chat/completions", json=_chat_body(temperature="high"))
    _assert_invalid_request(resp, "temperature")


def test_non_int_max_tokens_returns_400_with_param_location(client):
    resp = client.post("/v1/chat/completions", json=_chat_body(max_tokens="many"))
    _assert_invalid_request(resp, "max_tokens")


def test_invalid_stop_returns_400_with_param_location(client):
    resp = client.post("/v1/chat/completions", json=_chat_body(stop=42))
    _assert_invalid_request(resp, "stop")


def test_stream_true_returns_400_with_param_location(client):
    resp = client.post("/v1/chat/completions", json=_chat_body(stream=True))
    _assert_invalid_request(resp, "stream")


# ---------------------------------------------------------------------------
# 429：并发超限（max_concurrency=1 + 大脑 hang + 两个真实并发线程）
# ---------------------------------------------------------------------------


def test_concurrency_limit_rejects_excess_request_with_429(
    cerebellum, brain, brain_server, registry
):
    brain_server.mode = "hang"
    app = FastAPI()
    app.include_router(
        create_chat_router(cerebellum, brain, registry, max_concurrency=1)
    )

    results: list[tuple[int, dict]] = []

    def _fire() -> None:
        response = TestClient(app).post("/v1/chat/completions", json=_chat_body())
        results.append((response.status_code, response.json()))

    first = threading.Thread(target=_fire)
    first.start()
    # 轮询等待首个请求已真实抵达大脑（hang 中持有唯一并发额度），窗口内发起第二个必被拒
    deadline = time.monotonic() + 5.0
    while len(brain_server.requests) < 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(brain_server.requests) == 1

    second = threading.Thread(target=_fire)
    second.start()
    first.join(timeout=10.0)
    second.join(timeout=10.0)

    # 恰一个 200 一个 429
    assert sorted(status for status, _ in results) == [200, 429]
    by_status = dict(results)
    error = by_status[429]["error"]
    assert set(error) == {"message", "type", "code"}
    assert isinstance(error["message"], str) and error["message"]
