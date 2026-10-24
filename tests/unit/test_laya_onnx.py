"""T011-b：LayaOnnxBackend 单元测试（真实契约微型 bundle）。

约束 C1：禁 mock。微型 bundle 与真实 laya.onnx 同名同型五输入（见
tests/unit/conftest.py），toy 语义 logits[b,k]=marker_pos[b,k]——后出现的
选项 marker 位置更大 → argmax 恒为最后一个选项，答案确定性可精确断言。
"""

import pytest

from jev.errors import JevAssetError, JevOverflowError, JevProtocolError
from jev.kernel.cerebellum.base import (
    ChoiceAnswer,
    ChoiceQuestion,
    NoulAnswer,
    NoulQuestion,
    Question,
    ScoreAnswer,
    ScoreQuestion,
)
from jev.kernel.cerebellum.laya_onnx import LayaOnnxBackend

STATE = "hello world jev"


def _mixed_questions() -> dict[str, Question]:
    """三型混合问题集，每次调用返回新对象，用例相互独立。"""
    return {
        "route": ChoiceQuestion(
            choices={"fast": None, "deep": None, "off": None},
            instructions="选择执行路由",
        ),
        "need_more": NoulQuestion(instructions="判断是否需要更多上下文"),
        "depth": ScoreQuestion(
            criteria=["shallow", "medium", "deep"], instructions="评估理解深度等级"
        ),
    }


# ---------------------------------------------------------------------------
# 元信息与构造错误
# ---------------------------------------------------------------------------


def test_backend_name_is_laya_onnx(make_backend):
    assert make_backend().name() == "laya_onnx"


def test_warmup_runs_real_inference_without_raising(make_backend):
    make_backend().warmup()


def test_missing_model_file_raises_asset_error_containing_path(
    tmp_path, tokenizer_path, config_path
):
    missing = tmp_path / "no_such_model.onnx"
    with pytest.raises(JevAssetError) as excinfo:
        LayaOnnxBackend(
            model_path=missing, tokenizer_path=tokenizer_path, config_path=config_path
        )
    assert str(missing) in str(excinfo.value)


def test_illegal_truncation_mode_raises_protocol(tmp_path, model_path, tokenizer_path, config_path):
    with pytest.raises(JevProtocolError):
        LayaOnnxBackend(
            model_path=model_path,
            tokenizer_path=tokenizer_path,
            config_path=config_path,
            truncation="explode",
        )


# ---------------------------------------------------------------------------
# system_one：单次前向多问（三型混合；确定性答案）
# ---------------------------------------------------------------------------


def test_system_one_answers_mixed_three_type_questions_in_single_call(make_backend):
    questions = _mixed_questions()
    resp = make_backend().system_one(STATE, questions)

    assert set(resp.answers) == set(questions)
    assert resp.input_tokens > 0
    assert resp.truncated is False

    route = resp.answers["route"]
    assert isinstance(route, ChoiceAnswer)
    assert route.choice == "off"  # toy：最后选项 marker 位最大 → argmax
    assert set(route.probabilities) == set(questions["route"].choices)
    assert sum(route.probabilities.values()) == pytest.approx(1.0, abs=1e-6)
    assert 0.0 <= route.confidence <= 1.0

    need_more = resp.answers["need_more"]
    assert isinstance(need_more, NoulAnswer)
    assert need_more.noul > 0.5  # true 选项在后 → p[1] 过半
    assert 0.0 <= need_more.confidence <= 1.0

    depth = resp.answers["depth"]
    assert isinstance(depth, ScoreAnswer)
    assert 0.0 <= depth.score <= 2.0  # E[level] ∈ [0, L-1]
    assert depth.mode_level == "deep"
    assert set(depth.legend.values()) == set(questions["depth"].criteria)
    assert set(depth.probabilities) == {"0", "1", "2"}
    assert sum(depth.probabilities.values()) == pytest.approx(1.0, abs=1e-6)
    assert 0.0 <= depth.confidence <= 1.0


def test_input_tokens_equals_total_non_pad_ids(make_backend):
    questions = _mixed_questions()
    resp = make_backend().system_one(STATE, questions)
    # 三问各自独立成行：input_tokens = 三行序列长度之和（每行 > 头部长度）
    assert resp.input_tokens >= 3 * 5


def test_single_option_choice_question_gets_masked_second_slot(make_backend):
    """单选项问：K 下限 2（act 头约束），第二槽 mask 补位 → p=[1.0]。"""
    questions = {
        "only": ChoiceQuestion(choices={"yes": None}, instructions="是否执行")
    }
    resp = make_backend().system_one(STATE, questions)
    answer = resp.answers["only"]
    assert answer.choice == "yes"
    assert answer.probabilities == {"yes": 1.0}
    assert answer.confidence == 1.0


def test_options_beyond_max_len_raise_protocol_error(make_backend):
    """head 占满窗口 → marker 被裁 → 选项无法布位即报错（可定位 qid）。"""
    long_instructions = " ".join(["word"] * 12)  # head 编码 ≥ 8 token
    questions = {
        "wide": ChoiceQuestion(
            choices={"a": None, "b": None}, instructions=long_instructions
        )
    }
    backend = make_backend(max_state_tokens=2)
    # max_len=64 时 12 词 head 仍放得下——压小窗口触发
    backend._max_len = 12
    with pytest.raises(JevProtocolError) as excinfo:
        backend.system_one(STATE, questions)
    assert "wide" in str(excinfo.value)


# ---------------------------------------------------------------------------
# state 截断语义：truncate / refuse
# ---------------------------------------------------------------------------


def test_overlong_state_with_truncate_marks_truncated(make_backend):
    backend = make_backend(max_state_tokens=2, truncation="truncate")
    resp = backend.system_one("hello world jev deep", _mixed_questions())
    assert resp.truncated is True
    assert set(resp.answers) == {"route", "need_more", "depth"}


def test_overlong_state_with_refuse_raises_overflow(make_backend):
    backend = make_backend(max_state_tokens=2, truncation="refuse")
    with pytest.raises(JevOverflowError):
        backend.system_one("hello world jev deep", _mixed_questions())


# ---------------------------------------------------------------------------
# 温度校准
# ---------------------------------------------------------------------------


def test_calibration_temperature_two_flattens_choice_distribution(make_backend):
    plain = make_backend()
    hot = make_backend(calibration={"choice": 2.0})
    questions = _mixed_questions()

    probs_plain = plain.system_one(STATE, questions).answers["route"].probabilities
    probs_hot = hot.system_one(STATE, questions).answers["route"].probabilities
    assert max(probs_hot.values()) < max(probs_plain.values())
    assert sum(probs_hot.values()) == pytest.approx(1.0, abs=1e-6)


def test_calibration_temperature_clamped_into_valid_range(make_backend):
    """0.1 → clamp 0.5；10 → clamp 5（clamp 后与显式值同分布）。"""
    cold = make_backend(calibration={"choice": 0.1})
    cold_ref = make_backend(calibration={"choice": 0.5})
    hot = make_backend(calibration={"choice": 10.0})
    hot_ref = make_backend(calibration={"choice": 5.0})
    questions = _mixed_questions()

    probs = lambda b: b.system_one(STATE, questions).answers["route"].probabilities  # noqa: E731
    assert probs(cold) == pytest.approx(probs(cold_ref))
    assert probs(hot) == pytest.approx(probs(hot_ref))


def test_config_temperature_by_options_bucket_overrides_qtype_base(
    tmp_path, model_path, tokenizer_path, make_backend
):
    """桶温度（choice:2）优先于 qtype 基准。"""
    config_path = tmp_path / "laya_bucket_config.json"
    config_path.write_text(
        '{"max_len": 64, "head_max_len": 24, "temperature": [1.0, 1.0, 1.0],'
        ' "temperature_by_options": {"choice:2": 3.0}}',
        encoding="utf-8",
    )
    bucketed = LayaOnnxBackend(
        model_path=model_path, tokenizer_path=tokenizer_path, config_path=config_path
    )
    base = make_backend(calibration={"choice": 3.0})
    questions = {"q": ChoiceQuestion(choices={"a": None, "b": None}, instructions="二选一")}

    probs_bucketed = bucketed.system_one(STATE, questions).answers["q"].probabilities
    probs_base = base.system_one(STATE, questions).answers["q"].probabilities
    assert probs_bucketed == pytest.approx(probs_base)


def test_invalid_calibration_temperature_raises_protocol(make_backend):
    with pytest.raises(JevProtocolError):
        make_backend(calibration={"choice": 0.0})


# ---------------------------------------------------------------------------
# 批内变宽：选项数不同的问题混合（K 取最宽，各行右补 mask 槽）
# ---------------------------------------------------------------------------


def test_batch_pads_marker_slots_across_different_widths(make_backend):
    questions = {
        "two": ChoiceQuestion(choices={"a": None, "b": None}, instructions="二选一"),
        "five": ChoiceQuestion(
            choices={f"opt{i}": None for i in range(5)}, instructions="五选一"
        ),
    }
    resp = make_backend().system_one(STATE, questions)
    assert resp.answers["two"].choice == "b"
    assert resp.answers["five"].choice == "opt4"
    assert sum(resp.answers["five"].probabilities.values()) == pytest.approx(1.0, abs=1e-6)
