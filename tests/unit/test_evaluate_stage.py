"""AR002：阶段批量评估（evaluate_stage）——单次前向、融合、校准阈值覆盖。

C1：真实契约微型 ONNX 后端（conftest.make_backend），instrumentation 用
子类计数（真实后端 + 计数，不是 mock）。
"""

import pytest

from jev.kernel.cerebellum.base import SystemOneResponse
from jev.kernel.decisions.calibration import Calibration
from jev.kernel.decisions.evaluate import evaluate_stage
from jev.kernel.decisions.framework import DecisionPoint, Stage
from jev.kernel.decisions.probes import PointCalibration

POINTS = [
    DecisionPoint(
        id="intent", stage=Stage.PRE, question="choose intent", tau=0.6,
        fallback_action="complex", question_type="choice",
        options=("decision", "simple", "complex"), permutations=2,
    ),
    DecisionPoint(
        id="complexity", stage=Stage.PRE, question="how complex", tau=0.6,
        fallback_action="high", question_type="score",
        options=("low", "mid", "high"),
    ),
    DecisionPoint(
        id="safety", stage=Stage.PRE, question="is it safe", tau=0.8,
        fallback_action="unsafe", question_type="noul",
    ),
]


class _CountingBackend:
    """真实后端 + 调用计数（委托不改变语义；非 mock 模型）。"""

    def __init__(self, backend) -> None:
        self._backend = backend
        self.calls = 0
        self.last_questions: dict = {}

    def name(self) -> str:
        return self._backend.name()

    def warmup(self) -> None:
        self._backend.warmup()

    def system_one(self, state: str, questions: dict) -> SystemOneResponse:
        self.calls += 1
        self.last_questions = dict(questions)
        return self._backend.system_one(state, questions)


def test_single_forward_covers_all_probes_and_permutations(make_backend):
    backend = _CountingBackend(make_backend())
    result = evaluate_stage(backend, POINTS, "hello world jev")
    assert backend.calls == 1, "多探针/置换必须复用单次前向"
    # intent 2 置换 + complexity 1 + safety 1 = 4 个问题
    assert len(backend.last_questions) == 4
    assert len(result.decisions) == 3
    for decision in result.decisions:
        assert decision.margin is not None
        assert decision.probes is not None


def test_tiny_contract_backend_argmax_last_option(make_backend):
    backend = make_backend()
    single = [
        DecisionPoint(
            id="intent", stage=Stage.PRE, question="choose intent", tau=0.6,
            fallback_action="complex", question_type="choice",
            options=("decision", "simple", "complex"),
        ),
        POINTS[1],
        POINTS[2],
    ]
    result = evaluate_stage(backend, single, "hello world jev")
    by_id = {decision.point_id: decision for decision in result.decisions}
    # 微型契约模型 argmax 恒为最后选项（真实前向行为；单置换无语义漂移）
    assert by_id["intent"].value == "complex"
    assert by_id["complexity"].value == "high"
    assert by_id["safety"].from_fallback is False


def test_calibration_tau_override_applied(make_backend):
    backend = make_backend()
    calibration = Calibration(points={"intent": PointCalibration(temperature=1.0, tau=0.99)})
    result = evaluate_stage(backend, POINTS, "hello world jev", calibration=calibration)
    intent = next(d for d in result.decisions if d.point_id == "intent")
    assert intent.tau == pytest.approx(0.99)
    point = next(p for p in POINTS if p.id == "intent")
    assert intent.applied_value(point) == "complex"  # 0.99 阈值 → 安全侧 fallback


def test_missing_cerebellum_falls_back():
    result = evaluate_stage(None, POINTS, "hello world jev")
    assert all(decision.from_fallback for decision in result.decisions)
    assert [d.value for d in result.decisions] == ["complex", "high", "unsafe"]


def test_empty_points_returns_empty(make_backend):
    result = evaluate_stage(make_backend(), [], "hello")
    assert result.decisions == [] and result.answers == {} and result.latency_ms == 0.0
