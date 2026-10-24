"""T005：LlamaServerClient 测试——真实本地 http.server 作 OpenAI 兼容对端，禁 mock。

约束 C1：llama-server 真实推理属 assets 集成测试；本文件测 OpenAI 兼容客户端的
协议装配——POST /v1/chat/completions 请求体（model/messages/max_tokens/
temperature/stop）、响应解析（choices[0].message.content 与 usage）、错误映射
（连接失败/超时 → JevUnavailableError；非 2xx 或响应缺 choices/usage →
JevProtocolError）。服务器由标准库 http.server 实现，是真实协议对端而非
mock 模型；连接拒绝用已释放端口制造。
"""

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from jev.kernel.cerebrum.llama_server import LlamaServerClient

from jev.errors import JevProtocolError, JevUnavailableError

HANG_SECONDS = 1.0

MESSAGES = [
    {"role": "system", "content": "你是本地大脑"},
    {"role": "user", "content": "用一句话介绍自己"},
]

# 服务器侧的真实 OpenAI /v1/chat/completions 响应
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


class _ChatHandler(BaseHTTPRequestHandler):
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
            # 客户端已断开（超时用例主动放弃连接），对端写失败属预期
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
        elif server.mode == "missing_choices":
            self._send_json(200, {"id": "x", "usage": OPENAI_RESPONSE["usage"]})
        elif server.mode == "missing_usage":
            self._send_json(200, {"id": "x", "choices": OPENAI_RESPONSE["choices"]})
        elif server.mode == "hang":
            time.sleep(HANG_SECONDS)
            self._send_json(200, OPENAI_RESPONSE)
        else:
            self._send_json(200, OPENAI_RESPONSE)

    def do_GET(self):
        if self.path != "/health":
            self._send_json(404, {"error": "not found"})
        elif self.server.mode == "http500":
            self._send_json(500, {"status": "unhealthy"})
        else:
            self._send_json(200, {"status": "ok"})


class _RecordingServer(ThreadingHTTPServer):
    """记录请求的服务器；mode 切换协议对端行为。"""

    daemon_threads = True

    def __init__(self, address) -> None:
        super().__init__(address, _ChatHandler)
        self.mode = "ok"
        self.requests: list[dict] = []


@pytest.fixture()
def chat_server():
    server = _RecordingServer(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _base_url(server: _RecordingServer) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}"


def _released_port() -> int:
    """绑定后立即释放，制造真实连接拒绝场景。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# ---------------------------------------------------------------------------
# health：2xx 判定，不抛异常
# ---------------------------------------------------------------------------


def test_health_true_on_200(chat_server):
    client = LlamaServerClient(base_url=_base_url(chat_server))
    assert client.health() is True


def test_health_false_on_500(chat_server):
    chat_server.mode = "http500"
    client = LlamaServerClient(base_url=_base_url(chat_server))
    assert client.health() is False


def test_health_false_on_connection_refused():
    client = LlamaServerClient(base_url=f"http://127.0.0.1:{_released_port()}")
    assert client.health() is False


# ---------------------------------------------------------------------------
# chat：真实 HTTP 往返与 OpenAI 请求体装配
# ---------------------------------------------------------------------------


def test_chat_returns_parsed_openai_dict(chat_server):
    client = LlamaServerClient(base_url=_base_url(chat_server))

    result = client.chat(MESSAGES)

    assert result["choices"][0]["message"]["content"] == "本地大脑应答"
    assert result["usage"] == {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}


def test_chat_request_body_matches_openai_schema(chat_server):
    client = LlamaServerClient(base_url=_base_url(chat_server))

    client.chat(MESSAGES, max_tokens=64, temperature=0.2, stop=["END"])

    assert len(chat_server.requests) == 1
    request = chat_server.requests[0]
    assert request["path"] == "/v1/chat/completions"
    body = request["body"]
    assert body["model"] == "local"
    assert body["messages"] == MESSAGES
    assert body["max_tokens"] == 64
    assert body["temperature"] == 0.2
    assert body["stop"] == ["END"]


def test_chat_applies_default_parameters(chat_server):
    client = LlamaServerClient(base_url=_base_url(chat_server))

    client.chat(MESSAGES)

    body = chat_server.requests[0]["body"]
    assert body["model"] == "local"
    assert body["max_tokens"] == 512
    assert body["temperature"] == 0.7


# ---------------------------------------------------------------------------
# chat：错误映射
# ---------------------------------------------------------------------------


def test_chat_connection_refused_raises_unavailable():
    base_url = f"http://127.0.0.1:{_released_port()}"
    client = LlamaServerClient(base_url=base_url)
    with pytest.raises(JevUnavailableError):
        client.chat(MESSAGES)


def test_chat_timeout_raises_unavailable(chat_server):
    chat_server.mode = "hang"
    client = LlamaServerClient(base_url=_base_url(chat_server), timeout_s=0.2)
    with pytest.raises(JevUnavailableError):
        client.chat(MESSAGES)


def test_chat_server_500_raises_protocol(chat_server):
    chat_server.mode = "http500"
    client = LlamaServerClient(base_url=_base_url(chat_server))
    with pytest.raises(JevProtocolError):
        client.chat(MESSAGES)


def test_chat_missing_choices_raises_protocol(chat_server):
    chat_server.mode = "missing_choices"
    client = LlamaServerClient(base_url=_base_url(chat_server))
    with pytest.raises(JevProtocolError):
        client.chat(MESSAGES)


def test_chat_missing_usage_raises_protocol(chat_server):
    chat_server.mode = "missing_usage"
    client = LlamaServerClient(base_url=_base_url(chat_server))
    with pytest.raises(JevProtocolError):
        client.chat(MESSAGES)
