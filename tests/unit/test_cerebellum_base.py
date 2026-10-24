"""T003：小脑基础契约测试——三型问题/答案数据结构与置信度纯函数。

对应实现契约 src/jev/kernel/cerebellum/base.py：
- Question 三型：ChoiceQuestion / NoulQuestion / ScoreQuestion
- Answer 三型：ChoiceAnswer / NoulAnswer / ScoreAnswer（type 判别字段）
- SystemOneResponse（answers + input_tokens，truncated 缺省 False）
- CerebellumBackend 为抽象基类，不可直接实例化
- 模块级置信度纯函数：choice_confidence / noul_confidence / score_confidence
"""

import pytest

from jev.kernel.cerebellum.base import (
    CerebellumBackend,
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

# ---------------------------------------------------------------------------
# 数据结构：Question 三型
# ---------------------------------------------------------------------------


def test_choice_question_holds_choices_and_instructions():
    q = ChoiceQuestion(choices={"a": "Alpha 路径", "b": "Beta 路径"}, instructions="选择一条路径")
    assert q.choices == {"a": "Alpha 路径", "b": "Beta 路径"}
    assert q.instructions == "选择一条路径"


def test_noul_question_holds_instructions_and_optional_criteria():
    q_plain = NoulQuestion(instructions="判断是否需要补充上下文")
    assert q_plain.criteria is None
    q_described = NoulQuestion(
        instructions="判断是否需要补充上下文",
        criteria={"true": "需要补充", "false": "无需补充"},
    )
    assert q_described.criteria == {"true": "需要补充", "false": "无需补充"}


def test_score_question_holds_criteria_and_instructions():
    q = ScoreQuestion(criteria=["low", "mid", "high"], instructions="给出等级评定")
    assert q.criteria == ["low", "mid", "high"]
    assert q.instructions == "给出等级评定"


# ---------------------------------------------------------------------------
# 数据结构：Answer 三型
# ---------------------------------------------------------------------------


def test_choice_answer_carries_type_choice_and_probabilities():
    a = ChoiceAnswer(
        type="choice", choice="a", confidence=0.5, probabilities={"a": 0.75, "b": 0.25}
    )
    assert a.type == "choice"
    assert a.choice == "a"
    assert a.confidence == 0.5
    assert a.probabilities == {"a": 0.75, "b": 0.25}


def test_noul_answer_carries_type_noul_and_scalar():
    a = NoulAnswer(type="noul", noul=0.7, confidence=0.4)
    assert a.type == "noul"
    assert a.noul == 0.7
    assert a.confidence == 0.4


def test_score_answer_carries_expectation_legend_and_probabilities():
    a = ScoreAnswer(
        type="score",
        score=1.4,
        confidence=0.2,
        legend={"0": "low", "1": "mid", "2": "high"},
        probabilities={"0": 0.2, "1": 0.6, "2": 0.2},
    )
    assert a.type == "score"
    assert a.score == 1.4
    assert a.confidence == 0.2
    assert a.legend == {"0": "low", "1": "mid", "2": "high"}
    assert a.probabilities == {"0": 0.2, "1": 0.6, "2": 0.2}
    assert a.mode_level == "mid"  # 众数等级（argmax 概率的等级文本）


def test_system_one_response_holds_answers_and_input_tokens():
    answer = NoulAnswer(type="noul", noul=0.5, confidence=0.0)
    resp = SystemOneResponse(answers={"q1": answer}, input_tokens=7)
    assert set(resp.answers) == {"q1"}
    assert resp.answers["q1"] is answer
    assert resp.input_tokens == 7


def test_system_one_response_truncated_defaults_to_false():
    resp = SystemOneResponse(answers={}, input_tokens=0)
    assert resp.truncated is False


def test_cerebellum_backend_is_abstract_cannot_instantiate():
    with pytest.raises(TypeError):
        CerebellumBackend()


# ---------------------------------------------------------------------------
# choice_confidence：(p_max − 1/K) / (1 − 1/K)，K=1 恒 1.0
# ---------------------------------------------------------------------------


def test_choice_confidence_uniform_distribution_is_zero():
    probs = {"a": 1 / 3, "b": 1 / 3, "c": 1 / 3}
    assert choice_confidence(probs) == pytest.approx(0.0)


def test_choice_confidence_one_hot_distribution_is_one():
    probs = {"a": 1.0, "b": 0.0}
    assert choice_confidence(probs) == pytest.approx(1.0)


def test_choice_confidence_two_choice_intermediate_value():
    # (0.75 − 0.5) / (1 − 0.5) = 0.5
    probs = {"a": 0.75, "b": 0.25}
    assert choice_confidence(probs) == pytest.approx(0.5)


def test_choice_confidence_single_choice_is_always_one():
    # K=1 时分母退化，契约规定恒 1.0
    assert choice_confidence({"only": 1.0}) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# noul_confidence：2*max(p, 1−p) − 1
# ---------------------------------------------------------------------------


def test_noul_confidence_at_p_zero_is_one():
    assert noul_confidence(0.0) == pytest.approx(1.0)


def test_noul_confidence_at_p_half_is_zero():
    assert noul_confidence(0.5) == pytest.approx(0.0)


def test_noul_confidence_at_p_one_is_one():
    assert noul_confidence(1.0) == pytest.approx(1.0)


def test_noul_confidence_intermediate_values():
    assert noul_confidence(0.25) == pytest.approx(0.5)
    assert noul_confidence(0.75) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# score_confidence：max(0, 1 − E|level−mode| / D)
# D = 均匀分布各等级到中位等级 m=(L−1)/2 的平均距离，L=3 时 D=2/3
# ---------------------------------------------------------------------------


def test_score_confidence_uniform_three_levels_is_zero():
    # 均匀分布 → 置信度 0（无论 mode 取哪一级，E/D ≥ 1）
    probs = {"low": 1 / 3, "mid": 1 / 3, "high": 1 / 3}
    assert score_confidence(probs) == pytest.approx(0.0)


def test_score_confidence_all_mass_on_one_level_is_one():
    probs = {"low": 0.0, "mid": 1.0, "high": 0.0}
    assert score_confidence(probs) == pytest.approx(1.0)


def test_score_confidence_three_level_special_case_two_thirds():
    # L=3 → D=2/3；mode=mid 时 E|level−mode| = 1/9 + 1/9 = 2/9
    # 置信度 = 1 − (2/9)/(2/3) = 2/3
    probs = {"low": 1 / 9, "mid": 7 / 9, "high": 1 / 9}
    assert score_confidence(probs) == pytest.approx(2 / 3)


def test_score_confidence_clamps_negative_to_zero():
    # mode=high 时 E = 0.4*2 = 0.8 > D=2/3，裸值为负 → 截断到 0
    probs = {"low": 0.4, "mid": 0.0, "high": 0.6}
    assert score_confidence(probs) == 0.0


def test_score_confidence_two_levels_pin_distance_constant():
    # L=2 → D = (0.5 + 0.5)/2 = 0.5；mode=low 时 E = 0.25
    # 置信度 = 1 − 0.25/0.5 = 0.5
    probs = {"no": 0.75, "yes": 0.25}
    assert score_confidence(probs) == pytest.approx(0.5)
