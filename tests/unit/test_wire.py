"""T004/T011-a：TypeSafe 线协议共享编解码测试（wire.py）。

线协议对齐 kev/TypeSafe 参照（models/kev/repo/kev/api.py 同款）：
- 三型问题统一携带 criteria（choice: {label: 描述|null}；noul: {true,false} 可选；
  score: 有序等级文本数组）
- score 答案 = 期望等级浮点（E[level]）+ legend（等级索引→等级文本）+
  probabilities（等级索引→概率）
- confidence 由 base 置信度公式从分布计算（对齐 TypeSafe 参考适配器公式）
"""

import pytest

from jev.errors import JevProtocolError
from jev.kernel.cerebellum.base import (
    ChoiceAnswer,
    ChoiceQuestion,
    NoulAnswer,
    NoulQuestion,
    ScoreAnswer,
    ScoreQuestion,
    SystemOneResponse,
    choice_confidence,
    noul_confidence,
    score_confidence,
)
from jev.kernel.cerebellum.wire import decode_response, encode_request

MODEL = "kev-0.8b"
STATE = "用户希望在内网环境离线部署一个本地推理服务"

CHOICE_Q = ChoiceQuestion(
    choices={"direct": "小脑直答", "cascade": "级联大脑", "skip": None},
    instructions="选择应答路径",
)
NOUL_Q = NoulQuestion(
    instructions="判断是否需要补充上下文",
    criteria={"true": "需要补充", "false": "无需补充"},
)
NOUL_PLAIN_Q = NoulQuestion(instructions="判断是否需要补充上下文")
SCORE_Q = ScoreQuestion(criteria=["low", "mid", "high"], instructions="给出风险等级")


def _choice_answer_payload() -> dict:
    return {
        "type": "choice",
        "choice": "cascade",
        "probabilities": {"direct": 0.25, "cascade": 0.60, "skip": 0.15},
    }


def _noul_answer_payload() -> dict:
    return {"type": "noul", "noul": 0.8}


def _score_answer_payload() -> dict:
    return {
        "type": "score",
        "score": 1.1,
        "legend": {"0": "low", "1": "mid", "2": "high"},
        "probabilities": {"0": 0.1, "1": 0.7, "2": 0.2},
    }


def _valid_payload(answers: dict, **extra) -> dict:
    payload = {"answers": answers, "usage": {"input_tokens": 128}}
    payload.update(extra)
    return payload


# ---------------------------------------------------------------------------
# encode_request：三型问题 → 线格式精确断言（criteria 统一字段）
# ---------------------------------------------------------------------------


def test_encode_request_choice_question_wire_format():
    payload = encode_request(MODEL, STATE, {"q_path": CHOICE_Q})
    assert payload == {
        "model": MODEL,
        "state": STATE,
        "questions": {
            "q_path": {
                "type": "choice",
                "criteria": {"direct": "小脑直答", "cascade": "级联大脑", "skip": None},
                "instructions": "选择应答路径",
            }
        },
    }


def test_encode_request_noul_question_with_criteria_wire_format():
    payload = encode_request(MODEL, STATE, {"q_ctx": NOUL_Q})
    assert payload == {
        "model": MODEL,
        "state": STATE,
        "questions": {
            "q_ctx": {
                "type": "noul",
                "criteria": {"true": "需要补充", "false": "无需补充"},
                "instructions": "判断是否需要补充上下文",
            }
        },
    }


def test_encode_request_noul_question_without_criteria_omits_field():
    payload = encode_request(MODEL, STATE, {"q_ctx": NOUL_PLAIN_Q})
    assert payload["questions"]["q_ctx"] == {
        "type": "noul",
        "instructions": "判断是否需要补充上下文",
    }


def test_encode_request_score_question_wire_format():
    payload = encode_request(MODEL, STATE, {"q_risk": SCORE_Q})
    assert payload == {
        "model": MODEL,
        "state": STATE,
        "questions": {
            "q_risk": {
                "type": "score",
                "criteria": ["low", "mid", "high"],
                "instructions": "给出风险等级",
            }
        },
    }


# ---------------------------------------------------------------------------
# decode_response：正常三型 → Answer dataclass 字段回填
# ---------------------------------------------------------------------------


def test_decode_response_choice_answer_fields():
    resp = decode_response(_valid_payload({"q_path": _choice_answer_payload()}))
    assert isinstance(resp, SystemOneResponse)
    answer = resp.answers["q_path"]
    assert isinstance(answer, ChoiceAnswer)
    assert answer.type == "choice"
    assert answer.choice == "cascade"
    assert answer.probabilities == {"direct": 0.25, "cascade": 0.60, "skip": 0.15}
    assert answer.confidence == pytest.approx(
        choice_confidence({"direct": 0.25, "cascade": 0.60, "skip": 0.15})
    )


def test_decode_response_noul_answer_fields():
    resp = decode_response(_valid_payload({"q_ctx": _noul_answer_payload()}))
    answer = resp.answers["q_ctx"]
    assert isinstance(answer, NoulAnswer)
    assert answer.type == "noul"
    assert answer.noul == 0.8
    assert answer.confidence == pytest.approx(noul_confidence(0.8))


def test_decode_response_score_answer_fields():
    resp = decode_response(_valid_payload({"q_risk": _score_answer_payload()}))
    answer = resp.answers["q_risk"]
    assert isinstance(answer, ScoreAnswer)
    assert answer.type == "score"
    assert answer.score == pytest.approx(1.1)
    assert answer.legend == {"0": "low", "1": "mid", "2": "high"}
    assert answer.probabilities == {"0": 0.1, "1": 0.7, "2": 0.2}
    assert answer.mode_level == "mid"
    assert answer.confidence == pytest.approx(
        score_confidence({"0": 0.1, "1": 0.7, "2": 0.2})
    )


def test_decode_response_mixed_three_types_input_tokens_and_truncated_default():
    payload = _valid_payload(
        {
            "q_path": _choice_answer_payload(),
            "q_ctx": _noul_answer_payload(),
            "q_risk": _score_answer_payload(),
        }
    )
    resp = decode_response(payload)
    assert resp.input_tokens == 128
    assert resp.truncated is False
    assert set(resp.answers) == {"q_path", "q_ctx", "q_risk"}


def test_decode_response_truncated_true_is_preserved():
    payload = _valid_payload({"q_ctx": _noul_answer_payload()}, truncated=True)
    resp = decode_response(payload)
    assert resp.truncated is True


# ---------------------------------------------------------------------------
# decode_response：非法结构 → JevProtocolError（消息可定位）
# ---------------------------------------------------------------------------


def test_decode_response_missing_answers_raises_protocol():
    with pytest.raises(JevProtocolError) as exc_info:
        decode_response({"usage": {"input_tokens": 128}})
    assert "answers" in str(exc_info.value)


def test_decode_response_missing_usage_raises_protocol():
    payload = {"answers": {"q_ctx": _noul_answer_payload()}}
    with pytest.raises(JevProtocolError) as exc_info:
        decode_response(payload)
    assert "usage" in str(exc_info.value)


def test_decode_response_missing_input_tokens_raises_protocol():
    payload = {"answers": {"q_ctx": _noul_answer_payload()}, "usage": {}}
    with pytest.raises(JevProtocolError) as exc_info:
        decode_response(payload)
    assert "input_tokens" in str(exc_info.value)


def test_decode_response_non_integer_input_tokens_raises_protocol():
    payload = {
        "answers": {"q_ctx": _noul_answer_payload()},
        "usage": {"input_tokens": "128"},
    }
    with pytest.raises(JevProtocolError) as exc_info:
        decode_response(payload)
    assert "input_tokens" in str(exc_info.value)


def test_decode_response_unknown_answer_type_raises_protocol_with_qid():
    payload = _valid_payload({"q_bad": {"type": "ranking", "ranking": ["a", "b"]}})
    with pytest.raises(JevProtocolError) as exc_info:
        decode_response(payload)
    message = str(exc_info.value)
    assert "q_bad" in message
    assert "ranking" in message


def test_decode_response_choice_missing_probabilities_raises_protocol_with_qid():
    payload = _valid_payload({"q_bad": {"type": "choice", "choice": "direct"}})
    with pytest.raises(JevProtocolError) as exc_info:
        decode_response(payload)
    message = str(exc_info.value)
    assert "q_bad" in message
    assert "probabilities" in message


def test_decode_response_score_missing_legend_raises_protocol_with_qid():
    payload = _valid_payload(
        {"q_bad": {"type": "score", "score": 1.0, "probabilities": {"0": 1.0}}}
    )
    with pytest.raises(JevProtocolError) as exc_info:
        decode_response(payload)
    message = str(exc_info.value)
    assert "q_bad" in message
    assert "legend" in message


def test_decode_response_score_non_numeric_score_raises_protocol_with_qid():
    payload = _valid_payload(
        {
            "q_bad": {
                "type": "score",
                "score": "mid",
                "legend": {"0": "low", "1": "mid"},
                "probabilities": {"0": 0.5, "1": 0.5},
            }
        }
    )
    with pytest.raises(JevProtocolError) as exc_info:
        decode_response(payload)
    message = str(exc_info.value)
    assert "q_bad" in message
    assert "score" in message
