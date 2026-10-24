"""T014：shadow mode 全链路 + 对比报告——shadow 只记录不干预，反事实差异统计。

约束 C1：真实微型契约 bundle 小脑 + 真实 http.server OpenAI 协议大脑 +
真实台账落盘/检索 + 真实级联引擎重放。shadow 断言口径（srs FR4）：
台账记录决策（mode=shadow）但控制流不变；对比报告口径（design BS-03）：
shadow 决策若提升为 active，控制流（路由/MID/POST 裁决）是否会改变。
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jev.kernel.cascade.engine import CascadeEngine
from jev.kernel.cerebellum.laya_onnx import LayaOnnxBackend
from jev.kernel.cerebrum.llama_server import LlamaServerClient
from jev.kernel.decisions.framework import DecisionPoint, DecisionRegistry, Mode, Stage
from jev.kernel.ledger.query import LedgerQuery
from jev.kernel.ledger.report import shadow_report
from jev.kernel.ledger.writer import LedgerWriter
from jev.server.chat import create_chat_router


def _completion(content: str) -> dict:
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
        "usage": {"prompt_tokens": 10, "completion_tokens": 128, "total_tokens": 138},
    }


class _BrainHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        pass

    def do_POST(self):
        server = self.server
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        server.requests.append(body)
        data = json.dumps(_completion("第一段。")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class _BrainServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address) -> None:
        super().__init__(address, _BrainHandler)
        self.requests: list[dict] = []


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


class _CountingBackend(LayaOnnxBackend):
    """真实推理计数子类：锁定 active+shadow 合并单次前向。"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.question_counts: list[int] = []

    def system_one(self, state, questions):
        self.question_counts.append(len(questions))
        return super().system_one(state, questions)


@pytest.fixture()
def counting_backend(model_path, tokenizer_path, config_path):
    return _CountingBackend(
        model_path=model_path, tokenizer_path=tokenizer_path, config_path=config_path
    )


# ---------------------------------------------------------------------------
# shadow 注册表：active 组成 light 路由 + MID 继续 + POST 采纳；
# shadow 点若提升将翻转为 direct / STOP / RETRY（反事实口径）。
# toy 语义双保险：期望 applied 值同时取「末项」（conf≥τ 路径）与
# 「fallback」（conf<τ 路径），两种 regime 下语义一致。
# ---------------------------------------------------------------------------

# (intent, should_stop, needs_retry) 各自的选项序——末项即期望 applied 值
_FLIP_OPTIONS = (("simple", "decision"), ("continue", "stop"), ("no_retry", "retry"))
_NOFLIP_OPTIONS = (("decision", "simple"), ("stop", "continue"), ("retry", "no_retry"))


ShadowOptionPair = tuple[tuple[str, str], tuple[str, str], tuple[str, str]]


def _shadow_registry(options: ShadowOptionPair) -> DecisionRegistry:
    intent_opts, should_stop_opts, needs_retry_opts = options

    def point(pid, stage, opts, *, tau, fallback, mode=Mode.ACTIVE):
        return DecisionPoint(
            id=pid, stage=stage, question=f"{pid}: {opts[0]} or {opts[-1]}?",
            tau=tau, fallback_action=fallback, question_type="choice",
            options=opts, mode=mode,
        )

    registry = DecisionRegistry()
    # active PRE：needs_reasoning/complexity/safety → light 路由
    registry.register(
        point("needs_reasoning", Stage.PRE, ("reasoning", "no_reasoning"),
              tau=0.6, fallback="no_reasoning")
    )
    registry.register(
        point("complexity", Stage.PRE, ("high", "low"), tau=0.6, fallback="low")
    )
    registry.register(
        point("safety", Stage.PRE, ("unsafe", "safe"), tau=0.6, fallback="safe")
    )
    # shadow PRE：intent（提升后 → direct）
    registry.register(
        point("intent", Stage.PRE, intent_opts, tau=0.7,
              fallback=intent_opts[-1], mode=Mode.SHADOW)
    )
    # active MID：on_track；shadow MID：should_stop（提升后 → STOP）
    registry.register(
        point("on_track", Stage.MID, ("off_track", "on_track"),
              tau=0.6, fallback="on_track")
    )
    registry.register(
        point("should_stop", Stage.MID, should_stop_opts, tau=0.7,
              fallback=should_stop_opts[-1], mode=Mode.SHADOW)
    )
    # active POST：answer_complete；shadow POST：needs_retry（提升后 → RETRY）
    registry.register(
        point("answer_complete", Stage.POST, ("incomplete", "complete"),
              tau=0.7, fallback="complete")
    )
    registry.register(
        point("needs_retry", Stage.POST, needs_retry_opts, tau=0.6,
              fallback=needs_retry_opts[-1], mode=Mode.SHADOW)
    )
    return registry


def _chat(counting_backend, brain, registry, ledger, **body_overrides) -> dict:
    app = FastAPI()
    app.include_router(
        create_chat_router(
            counting_backend, brain, registry,
            engine=CascadeEngine(registry), ledger=ledger,
        )
    )
    client = TestClient(app)
    body = {"messages": [{"role": "user", "content": "hello world jev"}]}
    body.update(body_overrides)
    response = client.post("/v1/chat/completions", json=body)
    assert response.status_code == 200
    return response.json()


class TestShadowFullChain:
    def test_shadow_recorded_but_never_controls_flow(
        self, counting_backend, brain, brain_server, tmp_path
    ):
        """shadow intent=decision / should_stop=stop / needs_retry=retry 全部落台账，
        但实际控制流保持 light/继续/采纳（FR4：记录不变流）。"""
        registry = _shadow_registry(_FLIP_OPTIONS)
        with LedgerWriter(dir=tmp_path) as ledger:
            body = _chat(counting_backend, brain, registry, ledger, max_tokens=128)
            request_id = body["id"].removeprefix("chatcmpl-jev-")
            ledger.flush()

        # 实际控制流：light 单段生成、采纳交付（未重试/未降级）
        assert body["choices"][0]["message"]["content"] == "第一段。"
        assert "jev_degraded" not in body
        assert len(brain_server.requests) == 1

        entries = LedgerQuery(dir=tmp_path).query(request_id)
        by_point = {
            e.point_id: e for e in entries if e.kind == "decision" and e.point_id
        }
        # shadow 决策入台账：mode=shadow + applied 提升值
        assert by_point["intent"].mode == "shadow"
        assert by_point["intent"].applied == "decision"
        assert by_point["should_stop"].mode == "shadow"
        assert by_point["should_stop"].applied == "stop"
        assert by_point["needs_retry"].mode == "shadow"
        assert by_point["needs_retry"].applied == "retry"

        # active+shadow 同批单次前向：PRE 4 问 / MID 2 问 / POST 2 问
        assert counting_backend.question_counts == [4, 2, 2]

    def test_shadow_report_counts_counterfactual_flow_changes(
        self, counting_backend, brain, tmp_path
    ):
        """反事实报告：shadow 若当时生效 → direct/STOP/RETRY，三类差异全命中。"""
        registry = _shadow_registry(_FLIP_OPTIONS)
        with LedgerWriter(dir=tmp_path) as ledger:
            _chat(counting_backend, brain, registry, ledger, max_tokens=128)
            _chat(counting_backend, brain, registry, ledger, max_tokens=128)
            ledger.flush()

        entries = list(LedgerQuery(dir=tmp_path).iter_entries())
        report = shadow_report(entries, CascadeEngine(registry))

        assert report["requests"] == 2
        # 每请求三类控制流全部会被翻转
        assert len(report["flow_diff"]["route_changed"]) == 2
        assert len(report["flow_diff"]["mid_would_change"]) == 2
        assert len(report["flow_diff"]["post_would_change"]) == 2

        intent_stats = report["shadow_points"]["intent"]
        assert intent_stats["evaluated"] == 2
        assert intent_stats["values"] == {"decision": 2}
        assert 0.0 <= intent_stats["confidence_avg"] <= 1.0

    def test_shadow_report_no_diff_when_shadow_agrees(
        self, counting_backend, brain, tmp_path
    ):
        """shadow 与实际流一致（simple/continue/no_retry）→ 差异统计为空。"""
        registry = _shadow_registry(_NOFLIP_OPTIONS)
        with LedgerWriter(dir=tmp_path) as ledger:
            _chat(counting_backend, brain, registry, ledger, max_tokens=128)
            ledger.flush()

        entries = list(LedgerQuery(dir=tmp_path).iter_entries())
        report = shadow_report(entries, CascadeEngine(registry))
        assert report["requests"] == 1
        assert report["flow_diff"] == {
            "route_changed": [],
            "mid_would_change": [],
            "post_would_change": [],
        }
        # intent 每请求 1 次（PRE 批），与实际流一致 → 无差异
        assert report["shadow_points"]["intent"]["values"] == {"simple": 1}
        assert report["shadow_points"]["intent"]["evaluated"] == 1
