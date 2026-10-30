"""T013：级联引擎全链路 chat——三路径 + MID 早停 + POST 复核 + SSE 流式 + 台账。

约束 C1：禁 mock 后端。小脑为真实契约微型 bundle（conftest）；大脑为标准库
http.server 实现的真实 OpenAI 协议对端（非流式 canned 响应 + SSE 流式 chunk，
test_chat_endpoint.py 的 _RecordingServer 模式）；级联引擎/注册表/台账全部
真实实例。选项序 + fallback 双保险构造 toy 确定性 applied 值（conf 高低两
regime 下语义一致）。
"""

import dataclasses
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jev.kernel.cascade.draft import DraftConfig
from jev.kernel.cascade.engine import CascadeEngine
from jev.kernel.cerebellum.laya_onnx import LayaOnnxBackend
from jev.kernel.cerebrum.llama_server import LlamaServerClient
from jev.kernel.decisions.builtin import load_builtin_registry
from jev.kernel.decisions.calibration import Calibration
from jev.kernel.decisions.framework import DecisionPoint, DecisionRegistry, Stage
from jev.kernel.ledger.query import LedgerQuery
from jev.kernel.ledger.writer import LedgerWriter
from jev.kernel.runtime.config import RuntimeConfig
from jev.server.chat import (
    LIGHT_MID_CHUNKS,
    RETRY_FEEDBACK,
    create_chat_router,
)

# ---------------------------------------------------------------------------
# 真实大脑协议对端：非流式 canned / 流式 SSE，记录全部请求体
# ---------------------------------------------------------------------------


def _completion(content: str, *, completion_tokens: int = 128, prompt_tokens: int = 10) -> dict:
    return {
        "id": "chatcmpl-srv",
        "object": "chat.completion",
        "model": "local",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


class _BrainHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        pass

    def do_POST(self):
        server = self.server
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        server.requests.append({"path": self.path, "body": body})
        if getattr(server, "delay_s", 0.0):
            time.sleep(server.delay_s)  # EX-04：真实服务端延迟（非 mock 计时）
        if self.path != "/v1/chat/completions":
            self._send_json(404, {"error": "not found"})
        elif body.get("stream"):
            self._send_stream(server.stream_pieces)
        else:
            index = min(
                len([r for r in server.requests if not r["body"].get("stream")]) - 1,
                len(server.canned) - 1,
            )
            self._send_json(200, server.canned[index])

    def _send_json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            pass  # 客户端断开，对端写失败属预期

    def _send_stream(self, pieces: list[str]) -> None:
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            for piece in pieces:
                chunk = {
                    "id": "chatcmpl-srv",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "local",
                    "choices": [
                        {"index": 0, "delta": {"content": piece}, "finish_reason": None}
                    ],
                }
                self.wfile.write(
                    f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode()
                )
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            self.close_connection = True
        except OSError:
            self.close_connection = True


class _BrainServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address) -> None:
        super().__init__(address, _BrainHandler)
        self.requests: list[dict] = []
        self.canned: list[dict] = [_completion("本地大脑应答")]
        self.stream_pieces: list[str] = []


@pytest.fixture()
def brain_server():
    server = _BrainServer(("127.0.0.1", 0))
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
def cerebellum(model_path, tokenizer_path, config_path):
    return LayaOnnxBackend(
        model_path=model_path, tokenizer_path=tokenizer_path, config_path=config_path
    )


# ---------------------------------------------------------------------------
# toy 确定性注册表：期望 applied 值同时取「最后选项」（conf≥τ 路径）
# 与「fallback」（conf<τ 路径），两种 regime 语义一致
# ---------------------------------------------------------------------------


def _point(point_id: str, stage: Stage, options: tuple[str, ...], *, tau: float) -> DecisionPoint:
    return DecisionPoint(
        id=point_id,
        stage=stage,
        question=f"{point_id}: {options[0]} or {options[-1]}?",
        tau=tau,
        fallback_action=options[-1],
        question_type="choice",
        options=options,
    )


def _registry(points: list[DecisionPoint]) -> DecisionRegistry:
    registry = DecisionRegistry()
    for point in points:
        registry.register(point)
    return registry


def _direct_registry() -> DecisionRegistry:
    """intent→decision（τ 达标取末项/未达标取 fallback，均为 decision）→ 路径①。"""
    return _registry(
        [
            _point("intent", Stage.PRE, ("simple", "decision"), tau=0.7),
            _point("needs_reasoning", Stage.PRE, ("reasoning", "no_reasoning"), tau=0.6),
            _point("complexity", Stage.PRE, ("high", "low"), tau=0.6),
            _point("safety", Stage.PRE, ("unsafe", "safe"), tau=0.8),
        ]
    )


def _light_registry(*, mid_stop: bool) -> DecisionRegistry:
    """needs_reasoning=no_reasoning + complexity=low + safety=safe → 路径②。"""
    should_stop = (
        _point("should_stop", Stage.MID, ("continue", "stop"), tau=0.7)
        if mid_stop
        else _point("should_stop", Stage.MID, ("stop", "continue"), tau=0.7)
    )
    return _registry(
        [
            _point("intent", Stage.PRE, ("decision", "simple"), tau=0.7),
            _point("needs_reasoning", Stage.PRE, ("reasoning", "no_reasoning"), tau=0.6),
            _point("complexity", Stage.PRE, ("high", "low"), tau=0.6),
            _point("safety", Stage.PRE, ("unsafe", "safe"), tau=0.6),
            _point("on_track", Stage.MID, ("off_track", "on_track"), tau=0.6),
            _point("step_complete", Stage.MID, ("incomplete", "complete"), tau=0.6),
            should_stop,
            _point("needs_retry", Stage.POST, ("retry", "no_retry"), tau=0.6),
            _point("answer_complete", Stage.POST, ("incomplete", "complete"), tau=0.7),
            _point("matches_request", Stage.POST, ("off_topic", "matches"), tau=0.7),
        ]
    )


def _full_registry(*, post_retry: bool) -> DecisionRegistry:
    """intent≠decision + complexity≠low → 路径③；post_retry 控制 POST 复核走向。"""
    needs_retry = (
        ("no_retry", "retry") if post_retry else ("retry", "no_retry")
    )
    # 重试后裁决：post_retry 链路 answer_complete=incomplete → DOWNGRADE
    answer_complete = (
        ("complete", "incomplete") if post_retry else ("incomplete", "complete")
    )
    return _registry(
        [
            _point("intent", Stage.PRE, ("decision", "complex"), tau=0.7),
            _point("needs_reasoning", Stage.PRE, ("no_reasoning", "reasoning"), tau=0.6),
            _point("complexity", Stage.PRE, ("low", "high"), tau=0.6),
            _point("safety", Stage.PRE, ("unsafe", "safe"), tau=0.8),
            _point("needs_retry", Stage.POST, needs_retry, tau=0.6),
            _point("answer_complete", Stage.POST, answer_complete, tau=0.7),
            _point("matches_request", Stage.POST, ("off_topic", "matches"), tau=0.7),
        ]
    )


# ---------------------------------------------------------------------------
# 装配与请求构造
# ---------------------------------------------------------------------------


def _app(cerebellum, brain, registry, **router_kwargs) -> FastAPI:
    app = FastAPI()
    kwargs = dict(engine=CascadeEngine(registry))
    kwargs.update(router_kwargs)
    app.include_router(create_chat_router(cerebellum, brain, registry, **kwargs))
    return app


def _body(**overrides) -> dict:
    body: dict = {"messages": [{"role": "user", "content": "hello world jev"}]}
    body.update(overrides)
    return body


def _sse_chunks(resp) -> tuple[list[dict], bool]:
    """读取 SSE 流：返回 (data chunk 列表, 是否收到 [DONE])。"""
    chunks: list[dict] = []
    done = False
    for line in resp.iter_lines():
        if not line.startswith("data:"):
            continue
        data = line[len("data:"):].strip()
        if data == "[DONE]":
            done = True
            break
        chunks.append(json.loads(data))
    return chunks, done


def _delta_contents(chunks: list[dict]) -> list[str]:
    return [
        c["choices"][0]["delta"]["content"]
        for c in chunks
        if c.get("choices") and c["choices"][0].get("delta", {}).get("content")
    ]


# ---------------------------------------------------------------------------
# 路径① direct：小脑直答模板合成，不经大脑
# ---------------------------------------------------------------------------


class TestDirectRoute:
    def test_direct_without_brain_returns_structured_answer(self, cerebellum):
        """路径① 不依赖大脑：brain 未配置仍 200（FR5「不经大脑」严格口径）。"""
        registry = _direct_registry()
        client = TestClient(_app(cerebellum, None, registry))
        resp = client.post("/v1/chat/completions", json=_body())
        assert resp.status_code == 200
        body = resp.json()
        assert body["object"] == "chat.completion"
        assert body["model"] == "local"
        assert body["id"].startswith("chatcmpl-jev-")
        content = body["choices"][0]["message"]["content"]
        assert "- intent: decision" in content
        assert resp.headers["x-jev-route"] == "direct"
        assert resp.headers["x-jev-decisions"] == "4"

    def test_direct_never_calls_brain(self, cerebellum, brain, brain_server):
        registry = _direct_registry()
        client = TestClient(_app(cerebellum, brain, registry))
        resp = client.post("/v1/chat/completions", json=_body())
        assert resp.status_code == 200
        assert resp.headers["x-jev-route"] == "direct"
        assert brain_server.requests == []

    def test_render_direct_answer_lists_applied_values(self, cerebellum, registry=None):
        """UT-07：决策结果→结构化答案文本（纯函数口径，真实 Decision 对象）。"""
        registry = _direct_registry()
        client = TestClient(_app(cerebellum, None, registry))
        body = client.post("/v1/chat/completions", json=_body()).json()
        content = body["choices"][0]["message"]["content"]
        # 模板合成：每个 active PRE 点一行 applied 值 + 路径① 标注
        assert content.startswith("【Jev 小脑直答")
        assert "- safety: safe" in content
        assert "未经大脑" in content


# ---------------------------------------------------------------------------
# 路径② light：分段生成 + MID 早停
# ---------------------------------------------------------------------------


class TestLightRoute:
    def test_segments_generated_with_mid_continue(
        self, cerebellum, brain, brain_server
    ):
        """MID 持续 CONTINUE：按段预算生成全部段，段间以累积文本为 prefill。"""
        registry = _light_registry(mid_stop=False)
        brain_server.canned = [
            _completion("第一段。", completion_tokens=128),
            _completion("第二段。", completion_tokens=128),
        ]
        client = TestClient(_app(cerebellum, brain, registry))
        resp = client.post("/v1/chat/completions", json=_body(max_tokens=256))
        assert resp.status_code == 200
        body = resp.json()
        assert body["choices"][0]["message"]["content"] == "第一段。第二段。"
        assert resp.headers["x-jev-route"] == "light"
        # PRE4 + MID3×2段 + POST3
        assert resp.headers["x-jev-decisions"] == "13"
        # 两段各自真实请求大脑，段预算 128
        assert len(brain_server.requests) == 2
        assert brain_server.requests[0]["body"]["max_tokens"] == 128
        # 段间 prefill：第二段消息尾带累积 assistant 前缀
        second_messages = brain_server.requests[1]["body"]["messages"]
        assert second_messages[-1] == {"role": "assistant", "content": "第一段。"}
        # usage 跨段累计
        assert body["usage"]["completion_tokens"] == 256
        assert body["usage"]["prompt_tokens"] == 20

    def test_mid_early_stop_truncates_generation(self, cerebellum, brain, brain_server):
        """MID STOP：第一段后早停，不再请求后续段（控制流真实受决策影响）。"""
        registry = _light_registry(mid_stop=True)
        brain_server.canned = [
            _completion("第一段。", completion_tokens=128),
            _completion("第二段。", completion_tokens=128),
        ]
        client = TestClient(_app(cerebellum, brain, registry))
        resp = client.post("/v1/chat/completions", json=_body(max_tokens=256))
        assert resp.status_code == 200
        assert resp.json()["choices"][0]["message"]["content"] == "第一段。"
        assert len(brain_server.requests) == 1
        # PRE4 + MID3×1段 + POST3
        assert resp.headers["x-jev-decisions"] == "10"
        assert resp.headers["x-jev-route"] == "light"


# ---------------------------------------------------------------------------
# 路径③ full：单次生成 + POST 复核（一次重试上限 / 降级标注）
# ---------------------------------------------------------------------------


class TestFullRoutePost:
    def test_post_retry_once_then_downgrade(self, cerebellum, brain, brain_server):
        """needs_retry=retry → 附反馈重试一次；重试后 answer_complete=incomplete
        → 降级交付（jev_degraded=true），且只重试一次（FR5 验收口径）。"""
        registry = _full_registry(post_retry=True)
        brain_server.canned = [
            _completion("原始回答", completion_tokens=50),
            _completion("重试回答", completion_tokens=60),
        ]
        client = TestClient(_app(cerebellum, brain, registry))
        resp = client.post("/v1/chat/completions", json=_body(max_tokens=128))
        assert resp.status_code == 200
        body = resp.json()
        assert body["choices"][0]["message"]["content"] == "重试回答"
        assert body["jev_degraded"] is True
        assert resp.headers["x-jev-route"] == "full"
        # PRE4 + POST3×2轮
        assert resp.headers["x-jev-decisions"] == "10"
        # 两次真实大脑调用；重试请求附反馈指令
        assert len(brain_server.requests) == 2
        retry_messages = brain_server.requests[1]["body"]["messages"]
        assert retry_messages[-1] == {"role": "user", "content": RETRY_FEEDBACK}
        assert body["usage"]["completion_tokens"] == 110

    def test_post_accept_without_retry(self, cerebellum, brain, brain_server):
        """needs_retry=no_retry → 首轮采纳，单次大脑调用，无降级标注。"""
        registry = _full_registry(post_retry=False)
        brain_server.canned = [_completion("原始回答", completion_tokens=64)]
        client = TestClient(_app(cerebellum, brain, registry))
        resp = client.post("/v1/chat/completions", json=_body(max_tokens=128))
        assert resp.status_code == 200
        body = resp.json()
        assert body["choices"][0]["message"]["content"] == "原始回答"
        assert "jev_degraded" not in body
        assert len(brain_server.requests) == 1
        # PRE4 + POST3
        assert resp.headers["x-jev-decisions"] == "7"

    def test_degenerate_repetition_triggers_one_retry(
        self, cerebellum, brain, brain_server
    ):
        """退化重复输出（如 50745074...）即使 POST 判 no_retry 也重试一次。"""
        registry = _full_registry(post_retry=False)
        brain_server.canned = [
            _completion("5074507450745074", completion_tokens=16),
            _completion("正确回答", completion_tokens=64),
        ]
        client = TestClient(_app(cerebellum, brain, registry))
        resp = client.post("/v1/chat/completions", json=_body(max_tokens=128))
        assert resp.status_code == 200
        body = resp.json()
        assert body["choices"][0]["message"]["content"] == "正确回答"
        assert body["usage"]["completion_tokens"] == 80
        assert len(brain_server.requests) == 2

    def test_decision_permutation_budget_header(self, cerebellum, brain, brain_server):
        """decision_permutations>0 ?????????? margin ???????"""
        base = _full_registry(post_retry=False)
        registry = DecisionRegistry()
        for point in base.by_stage(Stage.PRE) + base.by_stage(Stage.POST):
            if point.id == "intent":
                point = dataclasses.replace(point, permutations=4)
            registry.register(point)
        brain_server.canned = [_completion("??", completion_tokens=16)]
        runtime = RuntimeConfig(
            enabled=True,
            decision_permutations=1,
            decision_escalate_margin=1.0,
        )
        client = TestClient(
            _app(cerebellum, brain, registry, runtime_config=runtime)
        )
        resp = client.post("/v1/chat/completions", json=_body(max_tokens=64))
        assert resp.status_code == 200
        assert resp.headers["x-jev-decision-permutations"] == "1"
        assert int(resp.headers["x-jev-decision-escalated"]) >= 1

    def test_staged_skips_pre_when_route_forced(self, cerebellum, brain, brain_server):
        """AR012???????????????PRE ?????"""
        registry = _full_registry(post_retry=False)
        brain_server.canned = [_completion("??", completion_tokens=16)]
        runtime = RuntimeConfig(
            enabled=True,
            decision_permutations=1,
            decision_staged=True,
        )
        client = TestClient(
            _app(
                cerebellum,
                brain,
                registry,
                runtime_config=runtime,
                route_policy=lambda pre: "full",
            )
        )
        resp = client.post("/v1/chat/completions", json=_body(max_tokens=64))
        assert resp.status_code == 200
        assert resp.headers["x-jev-decision-staged"] == "skipped"
        # ? POST 3 ? active ??PRE 4 ???????????? 7?
        assert resp.headers["x-jev-decisions"] == "3"
        assert resp.json()["choices"][0]["message"]["content"] == "??"

    def test_staged_minimal_accepts_full_without_confirmation(
        self, cerebellum, brain, brain_server
    ):
        """AR013??? wave1 ? FULL ? ???????????"""
        registry = _full_registry(post_retry=False)
        brain_server.canned = [_completion("full", completion_tokens=16)]
        runtime = RuntimeConfig(
            enabled=True,
            decision_permutations=1,
            decision_staged=True,
            decision_staged_minimal=True,
        )
        client = TestClient(
            _app(cerebellum, brain, registry, runtime_config=runtime)
        )
        resp = client.post("/v1/chat/completions", json=_body(max_tokens=64))
        assert resp.status_code == 200
        assert resp.headers["x-jev-route"] == "full"
        assert resp.headers["x-jev-decision-minimal"] == "on"
        assert resp.headers["x-jev-decision-staged"] == "on"

    def test_staged_minimal_confirms_cheap_route(
        self, cerebellum, brain, brain_server
    ):
        """AR013??? wave1 ? DIRECT ? ??? wave1 ?????"""
        registry = _direct_registry()
        brain_server.canned = [_completion("direct", completion_tokens=16)]
        runtime = RuntimeConfig(
            enabled=True,
            decision_permutations=1,
            decision_staged=True,
            decision_staged_minimal=True,
        )
        client = TestClient(
            _app(cerebellum, brain, registry, runtime_config=runtime)
        )
        resp = client.post("/v1/chat/completions", json=_body(max_tokens=64))
        assert resp.status_code == 200
        assert resp.headers["x-jev-route"] == "direct"
        assert resp.headers["x-jev-decision-minimal"] == "confirm"
        assert resp.headers["x-jev-decision-staged"] == "on"

    def test_staged_minimal_confirms_then_wave2(
        self, cerebellum, brain, brain_server
    ):
        """AR013????????? ? ?? wave2?????????"""
        registry = _registry(
            [
                _point("safety", Stage.PRE, ("unsafe", "safe"), tau=0.8),
                _point("intent", Stage.PRE, ("decision", "complex"), tau=0.7),
                _point(
                    "needs_reasoning",
                    Stage.PRE,
                    ("reasoning", "no_reasoning"),
                    tau=0.6,
                ),
                _point(
                    "needs_generation",
                    Stage.PRE,
                    ("generation", "no_generation"),
                    tau=0.6,
                ),
                _point("complexity", Stage.PRE, ("high", "low"), tau=0.6),
                _point("needs_retry", Stage.POST, ("retry", "no_retry"), tau=0.6),
                _point(
                    "answer_complete", Stage.POST, ("incomplete", "complete"), tau=0.7
                ),
                _point("matches_request", Stage.POST, ("off_topic", "matches"), tau=0.7),
            ]
        )
        brain_server.canned = [_completion("wave2", completion_tokens=16)]
        runtime = RuntimeConfig(
            enabled=True,
            decision_permutations=1,
            decision_staged=True,
            decision_staged_minimal=True,
        )
        client = TestClient(
            _app(cerebellum, brain, registry, runtime_config=runtime)
        )
        resp = client.post("/v1/chat/completions", json=_body(max_tokens=64))
        assert resp.status_code == 200
        assert resp.headers["x-jev-route"] == "direct"
        assert resp.headers["x-jev-decision-minimal"] == "confirm"
        # direct 路径跳过 POST：wave1 3 + wave2 2
        assert resp.headers["x-jev-decisions"] == "5"

    def test_long_context_int8_reasoning_gate_uses_one_decision(
        self, cerebellum, brain, brain_server
    ):
        """AR014??? state + int8 ?? ? ? reasoning 1 ??? FULL?"""
        registry = _full_registry(post_retry=False)
        brain_server.canned = [_completion("long", completion_tokens=16)]
        runtime = RuntimeConfig(
            enabled=True,
            decision_permutations=1,
            decision_staged=True,
            decision_staged_minimal=True,
            decision_long_context_tokens=1,
        )
        client = TestClient(
            _app(
                cerebellum,
                brain,
                registry,
                runtime_config=runtime,
                decision_int8=cerebellum,
            )
        )
        resp = client.post("/v1/chat/completions", json=_body(max_tokens=64))
        assert resp.status_code == 200
        assert resp.headers["x-jev-route"] == "full"
        assert resp.headers["x-jev-decision-long"] == "int8-reason-full:w384"
        assert resp.headers["x-jev-decision-staged"] == "on"
        # reasoning 1 + POST 3
        assert resp.headers["x-jev-decisions"] == "4"

    def test_builtin_registry_full_route_accepts(self, cerebellum, brain, brain_server):
        """内置 18 点真实配置 + toy：intent=complex → full；POST needs_retry=no_retry
        → 首轮采纳（真实 builtin 决策链路，PRE5+POST4）。"""
        registry = load_builtin_registry()
        brain_server.canned = [_completion("本地大脑应答", completion_tokens=64)]
        client = TestClient(_app(cerebellum, brain, registry))
        resp = client.post("/v1/chat/completions", json=_body())
        assert resp.status_code == 200
        body = resp.json()
        assert body["choices"][0]["message"]["content"] == "本地大脑应答"
        assert resp.headers["x-jev-route"] == "full"
        assert resp.headers["x-jev-decisions"] == "9"
        assert len(brain_server.requests) == 1

    def test_full_route_without_brain_returns_503(self, cerebellum):
        registry = load_builtin_registry()
        client = TestClient(_app(cerebellum, None, registry))
        resp = client.post("/v1/chat/completions", json=_body())
        assert resp.status_code == 503
        assert "retry-after" in resp.headers
        error = resp.json()["error"]
        assert error["type"] == "server_error"


# ---------------------------------------------------------------------------
# 台账：decision/route/answer 三类条目按 request_id 聚合
# ---------------------------------------------------------------------------


class TestLedgerWiring:
    def test_request_lands_decision_route_answer_entries(
        self, cerebellum, brain, brain_server, tmp_path
    ):
        registry = _light_registry(mid_stop=False)
        brain_server.canned = [
            _completion("第一段。", completion_tokens=128),
            _completion("第二段。", completion_tokens=128),
        ]
        with LedgerWriter(dir=tmp_path) as ledger:
            app = FastAPI()
            app.include_router(
                create_chat_router(
                    cerebellum, brain, registry,
                    engine=CascadeEngine(registry), ledger=ledger,
                )
            )
            client = TestClient(app)
            resp = client.post("/v1/chat/completions", json=_body(max_tokens=256))
            assert resp.status_code == 200
            body = resp.json()
            request_id = body["id"].removeprefix("chatcmpl-jev-")
            ledger.flush()

        entries = LedgerQuery(dir=tmp_path).query(request_id)
        kinds = [entry.kind for entry in entries]
        # PRE4 + MID3×2段 + POST3 = 13 条 decision
        assert kinds.count("decision") == 13
        assert kinds.count("route") == 1
        assert kinds.count("answer") == 1

        route_entry = next(e for e in entries if e.kind == "route")
        assert route_entry.route == "light"
        assert route_entry.posterior["route"] == "light"
        assert route_entry.state  # messages JSON 进入决策 state

        answer_entry = next(e for e in entries if e.kind == "answer")
        assert answer_entry.content == "第一段。第二段。"
        assert answer_entry.post_verdict == "accept"
        assert answer_entry.tokens == 256

        # decision 条目含 applied 门控值与真实置信度
        intent_entry = next(
            e for e in entries if e.kind == "decision" and e.point_id == "intent"
        )
        assert intent_entry.applied == "simple"
        assert 0.0 <= intent_entry.confidence <= 1.0


# ---------------------------------------------------------------------------
# SSE 流式：full 直传 / light MID 断流 / direct 模板切片
# ---------------------------------------------------------------------------


class TestStreaming:
    def test_full_route_relays_openai_chunks(self, cerebellum, brain, brain_server):
        registry = load_builtin_registry()
        brain_server.stream_pieces = ["你", "好", "！"]
        client = TestClient(_app(cerebellum, brain, registry))
        with client.stream(
            "POST", "/v1/chat/completions", json=_body(stream=True)
        ) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            assert resp.headers["x-jev-route"] == "full"
            chunks, done = _sse_chunks(resp)
        assert done
        assert "".join(_delta_contents(chunks)) == "你好！"
        assert all(c["object"] == "chat.completion.chunk" for c in chunks)
        assert all(c["model"] == "local" for c in chunks)
        assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
        # 流式请求真实抵达大脑（stream=true 透传）
        assert brain_server.requests[-1]["body"]["stream"] is True

    def test_light_route_mid_early_stop_cuts_stream(
        self, cerebellum, brain, brain_server
    ):
        """流式 light：每 LIGHT_MID_CHUNKS 个 chunk 评估 MID；STOP 即断流。"""
        registry = _light_registry(mid_stop=True)
        brain_server.stream_pieces = [f"块{i:02d}" for i in range(20)]
        client = TestClient(_app(cerebellum, brain, registry))
        with client.stream(
            "POST", "/v1/chat/completions", json=_body(stream=True)
        ) as resp:
            assert resp.headers["x-jev-route"] == "light"
            chunks, done = _sse_chunks(resp)
        assert done
        contents = _delta_contents(chunks)
        assert len(contents) == LIGHT_MID_CHUNKS
        assert "".join(contents) == "".join(f"块{i:02d}" for i in range(LIGHT_MID_CHUNKS))
        assert chunks[-1]["choices"][0]["finish_reason"] == "stop"

    def test_direct_route_streams_template_chunks(
        self, cerebellum, brain, brain_server
    ):
        registry = _direct_registry()
        client = TestClient(_app(cerebellum, brain, registry))
        with client.stream(
            "POST", "/v1/chat/completions", json=_body(stream=True)
        ) as resp:
            assert resp.headers["x-jev-route"] == "direct"
            chunks, done = _sse_chunks(resp)
        assert done
        joined = "".join(_delta_contents(chunks))
        assert "- intent: decision" in joined
        assert "未经大脑" in joined
        assert brain_server.requests == []


# ---------------------------------------------------------------------------
# EX-04（异常流程 3）：生成超时 → 截断 + POST 复核降级交付 jev_degraded
# ---------------------------------------------------------------------------


class TestGenerationTimeout:
    """generation_timeout_s（[cascade] 单源）：路径②/③ 总时长上限。"""

    def test_light_timeout_delivers_partial_degraded(
        self, cerebellum, brain, brain_server
    ):
        """路径② 段间超时：已生成部分截断交付，jev_degraded=true。"""
        brain_server.canned = [
            _completion("第一段。", completion_tokens=128),
            _completion("第二段。", completion_tokens=128),
        ]
        brain_server.delay_s = 0.3  # 真实服务端延迟：第一段后即超时
        registry = _light_registry(mid_stop=False)
        client = TestClient(
            _app(cerebellum, brain, registry, generation_timeout_s=0.4)
        )
        resp = client.post("/v1/chat/completions", json=_body(max_tokens=512))
        assert resp.status_code == 200
        body = resp.json()
        # 第一段完成、第二段前超时 → 截断为单段内容
        assert body["choices"][0]["message"]["content"] == "第一段。"
        assert body["jev_degraded"] is True
        assert resp.headers["x-jev-route"] == "light"
        # 第二段已发起（真实网络调用），在剩余 deadline 内被截断 → 只保留首段
        assert len(brain_server.requests) == 2
        assert "第二段。" not in body["choices"][0]["message"]["content"]
        brain_server.delay_s = 0.0

    def test_full_timeout_delivers_empty_degraded(self, cerebellum, brain_server):
        """路径③ 调用超时（客户端超时 < 服务端延迟且时限已到）：空内容降级交付。"""
        brain_server.canned = [_completion("完整长答案", completion_tokens=128)]
        brain_server.delay_s = 0.5
        brain = LlamaServerClient(
            base_url=f"http://127.0.0.1:{brain_server.server_address[1]}",
            timeout_s=0.2,
        )
        registry = _full_registry(post_retry=False)
        client = TestClient(
            _app(cerebellum, brain, registry, generation_timeout_s=0.1)
        )
        resp = client.post("/v1/chat/completions", json=_body(max_tokens=64))
        assert resp.status_code == 200
        body = resp.json()
        # 超时截断：无已生成内容 → 空内容 + POST 复核 + 降级标注
        assert body["choices"][0]["message"]["content"] == ""
        assert body["jev_degraded"] is True
        assert resp.headers["x-jev-route"] == "full"
        brain_server.delay_s = 0.0

    def test_brain_timeout_derived_from_deadline_without_client_tuning(
        self, cerebellum, brain, brain_server
    ):
        """FR2-4：客户端默认 120s 超时不再与 0.15s 级联截止竞态。

        旧行为下大脑 0.5s 后返回内容（超截止仍当成功）；现由剩余 deadline
        推导单次 HTTP 超时 → 0.15s 内截断并降级交付。
        """
        brain_server.canned = [_completion("迟到的回答", completion_tokens=8)]
        brain_server.delay_s = 0.5
        registry = _full_registry(post_retry=False)
        client = TestClient(
            _app(cerebellum, brain, registry, generation_timeout_s=0.15)
        )
        started = time.monotonic()
        resp = client.post("/v1/chat/completions", json=_body(max_tokens=64))
        elapsed = time.monotonic() - started
        assert resp.status_code == 200
        body = resp.json()
        assert body["choices"][0]["message"]["content"] == ""
        assert body["jev_degraded"] is True
        assert elapsed < 0.45  # 远早于大脑 0.5s 延迟
        brain_server.delay_s = 0.0

    def test_fast_failure_within_budget_is_503_not_degraded(self, cerebellum):
        """预算内快速失败（连接拒绝）是服务不可用 → 503，不是超时降级。"""
        dead_brain = LlamaServerClient(base_url="http://127.0.0.1:9", timeout_s=1.0)
        registry = _full_registry(post_retry=False)
        client = TestClient(
            _app(cerebellum, dead_brain, registry, generation_timeout_s=120.0)
        )
        resp = client.post("/v1/chat/completions", json=_body(max_tokens=64))
        assert resp.status_code == 503
        assert resp.headers.get("retry-after") == "1"

    def test_no_timeout_completes_normally(self, cerebellum, brain, brain_server):
        """无限时（None）：慢段生成照常完成（基线回归）。"""
        brain_server.canned = [
            _completion("第一段。", completion_tokens=128),
            _completion("第二段。", completion_tokens=128),
        ]
        brain_server.delay_s = 0.05
        registry = _light_registry(mid_stop=False)
        client = TestClient(_app(cerebellum, brain, registry))
        resp = client.post("/v1/chat/completions", json=_body(max_tokens=256))
        assert resp.status_code == 200
        body = resp.json()
        assert body["choices"][0]["message"]["content"] == "第一段。第二段。"
        assert "jev_degraded" not in body
        brain_server.delay_s = 0.0

# ---------------------------------------------------------------------------
# 路由遥测与请求级预算（AR002 FR2-1/FR2-2：T004）
# ---------------------------------------------------------------------------


class TestRoutingTelemetry:
    def test_budget_header_absent_when_disabled(self, cerebellum, brain, brain_server):
        brain_server.canned = [_completion("轻量回答。", completion_tokens=8)]
        registry = _light_registry(mid_stop=True)
        client = TestClient(_app(cerebellum, brain, registry))
        resp = client.post("/v1/chat/completions", json=_body(max_tokens=64))
        assert resp.status_code == 200
        assert resp.headers["x-jev-route"] == "light"
        assert "x-jev-budget" not in resp.headers
        assert "x-jev-margin" not in resp.headers

    def test_budget_blocks_full_and_marks_capped(
        self, cerebellum, brain, brain_server
    ):
        """预算不足以覆盖 full 估算 → 降级 light，并回 X-Jev-Budget: capped。"""
        brain_server.canned = [_completion("预算内回答。", completion_tokens=8)]
        registry = _full_registry(post_retry=False)
        client = TestClient(_app(cerebellum, brain, registry, max_brain_tokens=1))
        resp = client.post("/v1/chat/completions", json=_body(max_tokens=64))
        assert resp.status_code == 200
        assert resp.headers["x-jev-route"] == "light"
        assert resp.headers["x-jev-budget"] == "capped"
        # 预算内完成 token 上限被约束（至少 1，保证有进展）
        assert brain_server.requests[0]["body"]["max_tokens"] == 1

    def test_budget_ample_keeps_full_and_marks_ok(
        self, cerebellum, brain, brain_server
    ):
        brain_server.canned = [_completion("完整回答。", completion_tokens=8)]
        registry = _full_registry(post_retry=False)
        client = TestClient(
            _app(cerebellum, brain, registry, max_brain_tokens=100000)
        )
        resp = client.post("/v1/chat/completions", json=_body(max_tokens=64))
        assert resp.status_code == 200
        assert resp.headers["x-jev-route"] == "full"
        assert resp.headers["x-jev-budget"] == "ok"

    def test_margin_header_present_when_calibrated(
        self, cerebellum, brain, brain_server
    ):
        brain_server.canned = [_completion("校准回答。", completion_tokens=8)]
        registry = _light_registry(mid_stop=True)
        client = TestClient(
            _app(
                cerebellum,
                brain,
                registry,
                calibration=Calibration(margin_tau=0.18, accepted=True),
            )
        )
        resp = client.post("/v1/chat/completions", json=_body(max_tokens=64))
        assert resp.status_code == 200
        assert resp.headers["x-jev-margin"] == "0.180"

class TestDraftInjection:
    """AR002 FR2-3：草稿注入仅作用于 full 路径，默认关闭保持逐字行为。"""

    def _long_body(self):
        return _body(
            messages=[
                {"role": "user", "content": "第一轮问题：" + "细节" * 80},
                {"role": "assistant", "content": "第一轮回答：" + "说明" * 80},
                {"role": "user", "content": "第二轮问题：" + "细节" * 80},
                {"role": "assistant", "content": "第二轮回答：" + "说明" * 80},
                {"role": "user", "content": "请给出完整方案。"},
            ]
        )

    def test_disabled_keeps_messages_verbatim(self, cerebellum, brain, brain_server):
        brain_server.canned = [_completion("完整回答。", completion_tokens=8)]
        registry = _full_registry(post_retry=False)
        client = TestClient(_app(cerebellum, brain, registry))
        resp = client.post("/v1/chat/completions", json=self._long_body())
        assert resp.status_code == 200
        sent = brain_server.requests[0]["body"]["messages"]
        assert sent[0]["role"] == "user"
        assert all(m["role"] != "system" for m in sent)

    def test_enabled_injects_system_prefix_and_compresses_history(
        self, cerebellum, brain, brain_server
    ):
        brain_server.canned = [_completion("完整回答。", completion_tokens=8)]
        registry = _full_registry(post_retry=False)
        client = TestClient(
            _app(
                cerebellum,
                brain,
                registry,
                draft_config=DraftConfig(enabled=True, keep_turns=1),
            )
        )
        resp = client.post("/v1/chat/completions", json=self._long_body())
        assert resp.status_code == 200
        sent = brain_server.requests[0]["body"]["messages"]
        assert sent[0]["role"] == "system"
        assert sent[0]["content"].startswith("系统预判：")
        assert "intent=" in sent[0]["content"]
        # 超期轮次被压缩（≤120 字 + 省略号），最近一轮原文保留
        assert any(m["content"].endswith("…") for m in sent)
        assert sent[-1]["content"] == "请给出完整方案。"

    def test_light_route_not_affected(self, cerebellum, brain, brain_server):
        brain_server.canned = [_completion("轻量回答。", completion_tokens=8)]
        registry = _light_registry(mid_stop=True)
        client = TestClient(
            _app(
                cerebellum,
                brain,
                registry,
                draft_config=DraftConfig(enabled=True, keep_turns=1),
            )
        )
        resp = client.post("/v1/chat/completions", json=self._long_body())
        assert resp.status_code == 200
        sent = brain_server.requests[0]["body"]["messages"]
        assert all(m["role"] != "system" for m in sent)
