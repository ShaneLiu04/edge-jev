"""T004：KevHttpBackend 测试——真实本地 HTTP 服务器作 TypeSafe 协议对端，禁 mock。

约束 C1：kev 真实推理属 assets 集成测试；本文件测 HTTP 客户端的协议装配——
请求编码（POST /v1/systemone）、响应解码、错误映射（连接拒绝/超时/5xx →
JevUnavailableError；2xx 非法结构 → JevProtocolError）。服务器由标准库
http.server 实现，是真实协议对端而非 mock 模型；连接拒绝用已释放端口制造。
"""

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from jev.errors import JevProtocolError, JevUnavailableError
from jev.kernel.cerebellum.base import (
    ChoiceAnswer,
    ChoiceQuestion,
    NoulAnswer,
    NoulQuestion,
    ScoreAnswer,
    ScoreQuestion,
    choice_confidence,
    noul_confidence,
    score_confidence,
)
from jev.kernel.cerebellum.kev_http import KevHttpBackend
from jev.kernel.cerebellum.wire import encode_request

STATE = "用户希望在内网环境离线部署一个本地推理服务"
QUESTIONS = {
    "q_path": ChoiceQuestion(
        choices={"direct": "小脑直答", "cascade": "级联大脑"}, instructions="选择应答路径"
    ),
    "q_ctx": NoulQuestion(instructions="判断是否需要补充上下文"),
    "q_risk": ScoreQuestion(criteria=["low", "mid", "high"], instructions="给出风险等级"),
}

# 服务器侧的真实 TypeSafe /v1/systemone 响应（三型混合）
SERVER_RESPONSE = {
    "answers": {
        "q_path": {
            "type": "choice",
            "choice": "cascade",
            "probabilities": {"direct": 0.3, "cascade": 0.6},
        },
        "q_ctx": {"type": "noul", "noul": 0.85},
        "q_risk": {
            "type": "score",
            "score": 1.0,
            "legend": {"0": "low", "1": "mid", "2": "high"},
            "probabilities": {"0": 0.15, "1": 0.7, "2": 0.15},
        },
    },
    "usage": {"input_tokens": 96},
}

HANG_SECONDS = 1.0


class _TypeSafeHandler(BaseHTTPRequestHandler):
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
        if self.path != "/v1/systemone":
            self._send_json(404, {"error": "not found"})
        elif server.mode == "http500":
            self._send_json(500, {"error": "kev internal error"})
        elif server.mode == "bad_payload":
            self._send_json(200, {"usage": {"input_tokens": 1}})
        elif server.mode == "hang":
            time.sleep(HANG_SECONDS)
            self._send_json(200, SERVER_RESPONSE)
        else:
            self._send_json(200, SERVER_RESPONSE)

    def do_GET(self):
        server = self.server
        server.requests.append({"path": self.path, "body": None})
        # 对齐真实 kev.serve 路由表：只有 /v1/models，无 /health（ST-005 真实
        # 对端发现；warmup 必须探测真实存在的路由）
        if self.path != "/v1/models":
            self._send_json(404, {"error": "not found"})
        elif server.mode == "http500":
            self._send_json(500, {"error": "kev internal error"})
        else:
            self._send_json(200, {"models": [{"name": "kev-latest"}]})


class _RecordingServer(ThreadingHTTPServer):
    """记录请求的服务器；mode 切换协议对端行为。"""

    daemon_threads = True

    def __init__(self, address) -> None:
        super().__init__(address, _TypeSafeHandler)
        self.mode = "ok"
        self.requests: list[dict] = []


@pytest.fixture()
def kev_server():
    server = _RecordingServer(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _serve_url(server: _RecordingServer) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}"


def _released_port() -> int:
    """绑定后立即释放，制造真实连接拒绝场景。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# ---------------------------------------------------------------------------
# system_one：真实 HTTP 往返
# ---------------------------------------------------------------------------


def test_system_one_mixed_three_types_over_real_http(kev_server):
    backend = KevHttpBackend(serve_url=_serve_url(kev_server))
    resp = backend.system_one(STATE, QUESTIONS)
    assert resp.input_tokens == 96
    assert resp.truncated is False
    choice = resp.answers["q_path"]
    assert isinstance(choice, ChoiceAnswer)
    assert choice.type == "choice"
    assert choice.choice == "cascade"
    assert choice.probabilities == {"direct": 0.3, "cascade": 0.6}
    assert choice.confidence == pytest.approx(choice_confidence({"direct": 0.3, "cascade": 0.6}))
    noul = resp.answers["q_ctx"]
    assert isinstance(noul, NoulAnswer)
    assert noul.type == "noul"
    assert noul.noul == 0.85
    assert noul.confidence == pytest.approx(noul_confidence(0.85))
    score = resp.answers["q_risk"]
    assert isinstance(score, ScoreAnswer)
    assert score.type == "score"
    assert score.score == pytest.approx(1.0)  # E[level] = 0.15*0 + 0.7*1 + 0.15*2
    assert score.mode_level == "mid"
    assert score.legend == {"0": "low", "1": "mid", "2": "high"}
    assert score.probabilities == {"0": 0.15, "1": 0.7, "2": 0.15}
    assert score.confidence == pytest.approx(
        score_confidence({"0": 0.15, "1": 0.7, "2": 0.15})
    )


def test_system_one_request_body_matches_wire_format(kev_server):
    backend = KevHttpBackend(serve_url=_serve_url(kev_server))
    backend.system_one(STATE, QUESTIONS)
    assert len(kev_server.requests) == 1
    request = kev_server.requests[0]
    assert request["path"] == "/v1/systemone"
    assert request["body"] == encode_request("kev-0.8b", STATE, QUESTIONS)


def test_system_one_connection_refused_raises_unavailable():
    serve_url = f"http://127.0.0.1:{_released_port()}"
    backend = KevHttpBackend(serve_url=serve_url)
    with pytest.raises(JevUnavailableError) as exc_info:
        backend.system_one(STATE, QUESTIONS)
    assert serve_url in str(exc_info.value)


def test_system_one_server_500_raises_unavailable(kev_server):
    kev_server.mode = "http500"
    serve_url = _serve_url(kev_server)
    backend = KevHttpBackend(serve_url=serve_url)
    with pytest.raises(JevUnavailableError) as exc_info:
        backend.system_one(STATE, QUESTIONS)
    assert serve_url in str(exc_info.value)


def test_system_one_server_timeout_raises_unavailable(kev_server):
    kev_server.mode = "hang"
    serve_url = _serve_url(kev_server)
    backend = KevHttpBackend(serve_url=serve_url, timeout_s=0.2)
    with pytest.raises(JevUnavailableError) as exc_info:
        backend.system_one(STATE, QUESTIONS)
    assert serve_url in str(exc_info.value)


def test_system_one_invalid_payload_raises_protocol_error(kev_server):
    kev_server.mode = "bad_payload"
    backend = KevHttpBackend(serve_url=_serve_url(kev_server))
    with pytest.raises(JevProtocolError):
        backend.system_one(STATE, QUESTIONS)


# ---------------------------------------------------------------------------
# name / warmup
# ---------------------------------------------------------------------------


def test_name_is_kev(kev_server):
    backend = KevHttpBackend(serve_url=_serve_url(kev_server))
    assert backend.name() == "kev"


def test_warmup_models_200_passes(kev_server):
    backend = KevHttpBackend(serve_url=_serve_url(kev_server))
    backend.warmup()
    # warmup 必须探测真实 kev.serve 提供的 /v1/models（无 /health 路由）
    assert any(r["path"] == "/v1/models" for r in kev_server.requests if r["body"] is None)


def test_warmup_models_non_2xx_raises_unavailable(kev_server):
    kev_server.mode = "http500"
    backend = KevHttpBackend(serve_url=_serve_url(kev_server))
    with pytest.raises(JevUnavailableError):
        backend.warmup()


def test_warmup_connection_refused_raises_unavailable():
    serve_url = f"http://127.0.0.1:{_released_port()}"
    backend = KevHttpBackend(serve_url=serve_url)
    with pytest.raises(JevUnavailableError) as exc_info:
        backend.warmup()
    assert serve_url in str(exc_info.value)
