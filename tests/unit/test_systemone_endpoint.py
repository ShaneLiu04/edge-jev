"""T006：/v1/systemone 端点测试——真实 FastAPI TestClient（ASGI 往返）+ 真实 LayaOnnxBackend。

约束 C1：禁 mock 后端。真实契约微型 bundle 由 tests/unit/conftest.py 提供
（与真实 laya.onnx 同名同型五输入 toy 图 + tokenizers 微型词表）。
测试只断言 HTTP 行为（状态码/响应体/头），不关心端点内部函数。
"""

import pytest
from fastapi.testclient import TestClient

from jev.kernel.cerebellum import wire
from jev.kernel.cerebellum.base import (
    ChoiceQuestion,
    NoulQuestion,
    Question,
    ScoreQuestion,
    SystemOneResponse,
)
from jev.kernel.cerebellum.laya_onnx import LayaOnnxBackend
from jev.server.systemone import create_app

STATE = "hello world jev"


@pytest.fixture()
def make_backend(model_path, tokenizer_path, config_path):
    def _make(**kwargs):
        return LayaOnnxBackend(
            model_path=model_path,
            tokenizer_path=tokenizer_path,
            config_path=config_path,
            **kwargs,
        )

    return _make


@pytest.fixture()
def client(make_backend):
    return TestClient(create_app(make_backend()))


@pytest.fixture()
def refuse_client(make_backend):
    backend = make_backend(max_state_tokens=8, truncation="refuse")
    return TestClient(create_app(backend))


@pytest.fixture()
def truncate_client(make_backend):
    backend = make_backend(max_state_tokens=8, truncation="truncate")
    return TestClient(create_app(backend))


# ---------------------------------------------------------------------------
# 请求构造（线格式单源：合法请求经 wire.encode_request 生成）
# ---------------------------------------------------------------------------


def _mixed_questions() -> dict[str, Question]:
    """三型混合问题集，每次调用返回新对象，用例相互独立。"""
    return {
        "route": ChoiceQuestion(
            choices={"fast": "快速路径", "deep": "深度路径", "off": "不路由"},
            instructions="选择执行路由",
        ),
        "need_more": NoulQuestion(instructions="判断是否需要更多上下文"),
        "depth": ScoreQuestion(
            criteria=["shallow", "medium", "deep"], instructions="评估理解深度等级"
        ),
    }


def _valid_body(model: str = "laya_onnx", state: str = STATE) -> dict:
    return wire.encode_request(model, state, _mixed_questions())


def _body_with_questions(questions: object) -> dict:
    """绕过 wire 构造非法 questions 的请求体（错误矩阵用）。"""
    return {"model": "laya_onnx", "state": STATE, "questions": questions}


def _assert_error_body(resp, status: int, *fragments: str) -> str:
    """断言自定义错误体 {error:{message}}，message 含字段级定位片段，返回 message。"""
    assert resp.status_code == status
    body = resp.json()
    assert "error" in body
    message = body["error"]["message"]
    assert isinstance(message, str) and message
    for fragment in fragments:
        assert fragment in message, f"错误信息缺少定位片段 {fragment!r}: {message!r}"
    return message


# ---------------------------------------------------------------------------
# 200：三型混合请求，逐字段形状断言
# ---------------------------------------------------------------------------


def test_mixed_three_type_request_answers_each_question_shape(client):
    resp = client.post("/v1/systemone", json=_valid_body())
    assert resp.status_code == 200
    body = resp.json()

    assert body["model"] == "laya_onnx"
    input_tokens = body["usage"]["input_tokens"]
    assert isinstance(input_tokens, int) and not isinstance(input_tokens, bool)
    assert input_tokens > 0

    assert set(body["answers"]) == {"route", "need_more", "depth"}

    route = body["answers"]["route"]
    assert route["type"] == "choice"
    assert route["choice"] in {"fast", "deep", "off"}
    assert set(route["probabilities"]) == {"fast", "deep", "off"}
    assert sum(route["probabilities"].values()) == pytest.approx(1.0, abs=1e-5)
    assert 0.0 <= route["confidence"] <= 1.0

    need_more = body["answers"]["need_more"]
    assert need_more["type"] == "noul"
    assert isinstance(need_more["noul"], (int, float)) and not isinstance(need_more["noul"], bool)
    assert 0.0 <= need_more["noul"] <= 1.0

    depth = body["answers"]["depth"]
    assert depth["type"] == "score"
    assert isinstance(depth["score"], (int, float)) and not isinstance(depth["score"], bool)
    assert 0.0 <= depth["score"] <= 2.0  # E[level] ∈ [0, L-1]
    assert set(depth["legend"]) == {"0", "1", "2"}
    assert set(depth["legend"].values()) == {"shallow", "medium", "deep"}
    assert set(depth["probabilities"]) == {"0", "1", "2"}
    assert sum(depth["probabilities"].values()) == pytest.approx(1.0, abs=1e-5)
    assert 0.0 <= depth["confidence"] <= 1.0


def test_unknown_request_model_is_rewritten_to_actual_backend_name(client):
    resp = client.post("/v1/systemone", json=_valid_body(model="unknown-model-xyz"))
    assert resp.status_code == 200
    assert resp.json()["model"] == "laya_onnx"


def test_dict_state_is_accepted_and_answered(client):
    body = _valid_body()
    body["state"] = {"context": "hello world jev"}
    resp = client.post("/v1/systemone", json=body)
    assert resp.status_code == 200
    assert set(resp.json()["answers"]) == {"route", "need_more", "depth"}


# ---------------------------------------------------------------------------
# 超长 state：truncate → 200 + truncated:true；refuse → 422
# ---------------------------------------------------------------------------


def test_overlong_state_with_truncate_backend_returns_truncated_true(truncate_client):
    overlong = " ".join(["hello"] * 20)
    resp = truncate_client.post("/v1/systemone", json=_valid_body(state=overlong))
    assert resp.status_code == 200
    assert resp.json()["truncated"] is True


def test_overlong_state_with_refuse_backend_returns_422(refuse_client):
    overlong = " ".join(["hello"] * 20)
    resp = refuse_client.post("/v1/systemone", json=_valid_body(state=overlong))
    _assert_error_body(resp, 422)


# ---------------------------------------------------------------------------
# 错误矩阵：400 + 字段级定位
# ---------------------------------------------------------------------------


def test_non_object_body_returns_400(client):
    resp = client.post("/v1/systemone", json=["not", "an", "object"])
    _assert_error_body(resp, 400)


def test_missing_model_returns_400_with_field_location(client):
    body = _valid_body()
    del body["model"]
    _assert_error_body(client.post("/v1/systemone", json=body), 400, "model")


def test_missing_state_returns_400_with_field_location(client):
    body = _valid_body()
    del body["state"]
    _assert_error_body(client.post("/v1/systemone", json=body), 400, "state")


def test_missing_questions_returns_400_with_field_location(client):
    body = _valid_body()
    del body["questions"]
    _assert_error_body(client.post("/v1/systemone", json=body), 400, "questions")


def test_empty_questions_returns_400(client):
    resp = client.post("/v1/systemone", json=_body_with_questions({}))
    _assert_error_body(resp, 400, "questions")


def test_non_object_questions_returns_400(client):
    _assert_error_body(
        client.post("/v1/systemone", json=_body_with_questions(["q1"])), 400, "questions"
    )


def test_illegal_question_type_returns_400_with_question_location(client):
    questions = {"q1": {"type": "boolean", "instructions": "判断"}}
    _assert_error_body(
        client.post("/v1/systemone", json=_body_with_questions(questions)), 400, "q1", "type"
    )


def test_missing_instructions_returns_400_with_question_location(client):
    questions = {"q1": {"type": "noul"}}
    _assert_error_body(
        client.post("/v1/systemone", json=_body_with_questions(questions)),
        400,
        "q1",
        "instructions",
    )


def test_choice_question_missing_criteria_returns_400(client):
    questions = {"q1": {"type": "choice", "instructions": "选择"}}
    _assert_error_body(
        client.post("/v1/systemone", json=_body_with_questions(questions)), 400, "q1", "criteria"
    )


def test_choice_question_legacy_choices_field_returns_400(client):
    """旧字段名 choices 不再受支持：统一 criteria（TypeSafe 对齐）。"""
    questions = {
        "q1": {
            "type": "choice",
            "choices": {"a": "甲", "b": "乙"},
            "instructions": "选择",
        }
    }
    _assert_error_body(
        client.post("/v1/systemone", json=_body_with_questions(questions)), 400, "q1", "criteria"
    )


def test_score_question_empty_criteria_returns_400(client):
    questions = {"q1": {"type": "score", "criteria": [], "instructions": "评分"}}
    _assert_error_body(
        client.post("/v1/systemone", json=_body_with_questions(questions)), 400, "q1", "criteria"
    )


# ---------------------------------------------------------------------------
# 服务未就绪：backend=None → 503 + Retry-After
# ---------------------------------------------------------------------------


def test_none_backend_returns_503_with_retry_after():
    resp = TestClient(create_app(None)).post("/v1/systemone", json=_valid_body())
    assert resp.status_code == 503
    assert "retry-after" in resp.headers
    body = resp.json()
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]


# ---------------------------------------------------------------------------
# 线格式自洽：端点 200 响应体可经 wire.decode_response 还原
# ---------------------------------------------------------------------------


def test_response_body_roundtrips_through_wire_decode_response(client):
    resp = client.post("/v1/systemone", json=_valid_body())
    assert resp.status_code == 200
    body = resp.json()

    decoded = wire.decode_response(body)
    assert isinstance(decoded, SystemOneResponse)
    assert decoded.input_tokens == body["usage"]["input_tokens"]
    assert set(decoded.answers) == {"route", "need_more", "depth"}


# ---------------------------------------------------------------------------
# EX-09：100 并发 systemone 端到端——全部 200、无错误响应
# ---------------------------------------------------------------------------


def test_100_concurrent_systemone_all_succeed(make_backend):
    """100 并发（真实 asyncio + ASGI + 真 ONNX 会话并发前向）全部 200。"""
    import asyncio

    import httpx

    async def _run() -> list[int]:
        app = create_app(make_backend())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://jev-test", timeout=30.0
        ) as http:
            bodies = [_valid_body() for _ in range(100)]
            responses = await asyncio.gather(
                *(http.post("/v1/systemone", json=b) for b in bodies)
            )
            return [r.status_code for r in responses]

    statuses = asyncio.run(_run())
    assert len(statuses) == 100
    assert statuses == [200] * 100
