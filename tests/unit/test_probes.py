"""AR002 T001/T002：多探针规划、置换、融合（纯逻辑 + 真实契约微型后端）。

C1：不 mock 后端。规划/融合为纯逻辑；评估用例使用 conftest 的真实契约
微型 ONNX bundle（argmax 恒为最后选项，确定性可断言）。
"""

import pytest

from jev.errors import JevProtocolError
from jev.kernel.cerebellum.base import ChoiceAnswer, NoulAnswer, ScoreAnswer
from jev.kernel.decisions.framework import DecisionPoint, Stage
from jev.kernel.decisions.probes import (
    MAX_PERMUTATIONS,
    PointCalibration,
    ProbeSpec,
    fuse_point,
    plan_questions,
)


def _point(**kwargs) -> DecisionPoint:
    base = dict(
        id="intent",
        stage=Stage.PRE,
        question="choose intent",
        tau=0.6,
        fallback_action="complex",
        question_type="choice",
        options=("decision", "simple", "complex"),
    )
    base.update(kwargs)
    return DecisionPoint(**base)


class TestPlanQuestions:
    def test_single_probe_keeps_legacy_qid(self):
        point = _point()
        questions, plans = plan_questions([point])
        assert list(questions) == ["intent"]
        assert plans[0].qids == ("intent",)
        question = questions["intent"]
        assert tuple(question.choices) == ("decision", "simple", "complex")

    def test_permutations_generate_distinct_orders_deterministically(self):
        point = _point(permutations=4)
        questions, plans = plan_questions([point], seed=7)
        assert len(questions) == 4
        orders = {plan.permuted[qid] for plan in plans for qid in plan.qids}
        assert len(orders) == 4  # 4 种不同顺序
        assert all(set(order) == {"decision", "simple", "complex"} for order in orders)
        # 同种子逐位可复现；不同种子不同顺序
        again, _ = plan_questions([point], seed=7)
        assert [tuple(q.choices) for q in again.values()] == [
            tuple(q.choices) for q in questions.values()
        ]
        other, _ = plan_questions([point], seed=8)
        assert [tuple(q.choices) for q in other.values()] != [
            tuple(q.choices) for q in questions.values()
        ]

    def test_multi_probe_plan_covers_all_probes(self):
        point = _point(
            probes=(
                ProbeSpec("default", "choice", "choose intent", ("decision", "simple", "complex")),
                ProbeSpec("pair", "noul", "is this a pure decision request?", ()),
            )
        )
        questions, plans = plan_questions([point])
        assert len(questions) == 2
        assert set(plans[0].probe_of.values()) == {"default", "pair"}

    def test_permutations_validation(self):
        with pytest.raises(JevProtocolError, match="permutations"):
            plan_questions([_point(permutations=MAX_PERMUTATIONS + 1)])
        with pytest.raises(JevProtocolError, match="仅 choice"):
            plan_questions([_point(question_type="score", permutations=2)])
        with pytest.raises(JevProtocolError, match="weight"):
            plan_questions(
                [_point(probes=(ProbeSpec("p", "choice", "q", ("a", "b"), weight=0),))]
            )


def _choice(qid: str, probabilities: dict) -> ChoiceAnswer:
    return ChoiceAnswer(
        type="choice", choice=max(probabilities, key=probabilities.__getitem__),
        probabilities=probabilities, confidence=max(probabilities.values()),
    )


class TestFuse:
    def test_permutation_average_is_order_invariant(self):
        point = _point(permutations=2)
        questions, plans = plan_questions([point], seed=3)
        qids = list(questions)
        first, second = qids
        answers = {
            first: _choice(first, {"decision": 0.6, "simple": 0.3, "complex": 0.1}),
            second: _choice(second, {"complex": 0.2, "simple": 0.2, "decision": 0.6}),
        }
        fused = fuse_point(plans[0], answers, (ProbeSpec(
            "default", "choice", "choose intent", ("decision", "simple", "complex")),))
        assert fused is not None
        assert fused.value == "decision"
        # 两个置换按选项名归并：decision (0.6+0.6)/2 = 0.6
        assert fused.distribution["decision"] == pytest.approx(0.6, abs=1e-9)
        assert fused.confidence == pytest.approx(0.6, abs=1e-9)
        assert fused.margin == pytest.approx(0.35, abs=1e-9)  # 0.6 - 0.25

    def test_weighted_probe_bias(self):
        point = _point(
            probes=(
                ProbeSpec("strong", "choice", "q", ("a", "b"), weight=3.0),
                ProbeSpec("weak", "choice", "q", ("a", "b"), weight=1.0),
            )
        )
        questions, plans = plan_questions([point])
        qids = list(questions)
        by_probe = {plans[0].probe_of[qid]: qid for qid in qids}
        answers = {
            by_probe["strong"]: _choice(by_probe["strong"], {"a": 0.9, "b": 0.1}),
            by_probe["weak"]: _choice(by_probe["weak"], {"a": 0.2, "b": 0.8}),
        }
        fused = fuse_point(plans[0], answers, point.probes)
        assert fused.value == "a"  # 高权重探针主导

    def test_temperature_softens_distribution(self):
        point = _point()
        questions, plans = plan_questions([point])
        qid = next(iter(questions))
        answers = {qid: _choice(qid, {"decision": 0.9, "simple": 0.05, "complex": 0.05})}
        specs = (ProbeSpec("default", "choice", "q", ("decision", "simple", "complex")),)
        sharp = fuse_point(plans[0], answers, specs)
        soft = fuse_point(plans[0], answers, specs, PointCalibration(temperature=2.0))
        assert soft.confidence < sharp.confidence

    def test_noul_fusion(self):
        point = _point(
            id="safety", question_type="noul", options=(), fallback_action="unsafe",
            probes=(ProbeSpec("p", "noul", "is it safe?"),),
        )
        questions, plans = plan_questions([point])
        qid = next(iter(questions))
        answers = {qid: NoulAnswer(type="noul", noul=0.9, confidence=0.9)}
        fused = fuse_point(plans[0], answers, point.probes)
        assert fused.distribution == {"true": pytest.approx(0.9), "false": pytest.approx(0.1)}
        assert fused.confidence == pytest.approx(0.9)
        assert fused.margin == pytest.approx(0.8)

    def test_score_fusion_maps_levels(self):
        point = _point(
            id="complexity", question_type="score", options=("low", "mid", "high"),
            fallback_action="high",
            probes=(ProbeSpec("p", "score", "how complex?", ("low", "mid", "high")),),
        )
        questions, plans = plan_questions([point])
        qid = next(iter(questions))
        answers = {
            qid: ScoreAnswer(
                type="score", score=2.0,
                legend={"0": "low", "1": "mid", "2": "high"},
                probabilities={"0": 0.1, "1": 0.2, "2": 0.7},
                confidence=0.7,
            )
        }
        fused = fuse_point(plans[0], answers, point.probes)
        assert fused.value == "high"
        assert fused.distribution["high"] == pytest.approx(0.7)

    def test_all_answers_missing_returns_none(self):
        point = _point()
        _, plans = plan_questions([point])
        specs = (ProbeSpec("default", "choice", "q", ("decision", "simple", "complex")),)
        assert fuse_point(plans[0], {}, specs) is None
