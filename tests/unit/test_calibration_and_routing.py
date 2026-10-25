"""AR002 FR1-3：校准 v1/v2 加载 + FR2-1/FR2-2 路由 margin 与预算。"""

import json

import pytest

from jev.errors import JevAssetError
from jev.kernel.cascade.budget import BudgetContext
from jev.kernel.cascade.engine import CascadeEngine, Route
from jev.kernel.decisions.calibration import load_calibration
from jev.kernel.decisions.framework import Decision, DecisionPoint, Mode, Stage


def _write(tmp_path, payload) -> str:
    path = tmp_path / "calibration.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


class TestCalibrationLoad:
    def test_v1_backward_compatible(self, tmp_path):
        path = _write(tmp_path, {
            "temperature": {"choice": 1.2, "score": 1.1, "noul": 1.0},
            "accepted": True,
        })
        calibration = load_calibration(path)
        assert calibration.temperatures["choice"] == pytest.approx(1.2)
        assert calibration.for_point("intent", "choice").temperature == pytest.approx(1.2)
        assert calibration.margin_tau == 0.0

    def test_v2_per_point_and_routing(self, tmp_path):
        path = _write(tmp_path, {
            "schema": 2,
            "accepted": True,
            "global": {"temperature": {"choice": 1.5}},
            "points": {"intent": {"temperature": 1.31, "tau": 0.55, "margin": 0.18}},
            "routing": {"margin_tau": 0.18},
        })
        calibration = load_calibration(path)
        intent = calibration.for_point("intent", "choice")
        assert intent.temperature == pytest.approx(1.31)
        assert intent.tau == pytest.approx(0.55)
        assert intent.margin == pytest.approx(0.18)
        # 未声明点回落全局题型温度
        assert calibration.for_point("complexity", "choice").temperature == pytest.approx(1.5)
        assert calibration.margin_tau == pytest.approx(0.18)

    def test_rejected_calibration_not_loaded(self, tmp_path):
        path = _write(tmp_path, {"temperature": {"choice": 2.0}, "accepted": False})
        assert load_calibration(path).is_empty

    def test_missing_file_is_empty(self, tmp_path):
        assert load_calibration(tmp_path / "nope.json").is_empty
        assert load_calibration(None).is_empty

    def test_invalid_json_raises(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(JevAssetError, match="不合法"):
            load_calibration(path)

    def test_unknown_schema_raises(self, tmp_path):
        path = _write(tmp_path, {"schema": 9, "accepted": True})
        with pytest.raises(JevAssetError, match="schema"):
            load_calibration(path)

    def test_margin_tau_out_of_range_raises(self, tmp_path):
        path = _write(tmp_path, {"schema": 2, "accepted": True, "routing": {"margin_tau": 1.5}})
        with pytest.raises(JevAssetError, match="margin_tau"):
            load_calibration(path)


def _point(**kwargs) -> DecisionPoint:
    base = dict(
        id="intent", stage=Stage.PRE, question="q", tau=0.6,
        fallback_action="complex", question_type="choice",
        options=("decision", "simple", "complex"),
    )
    base.update(kwargs)
    return DecisionPoint(**base)


def _decision(point_id: str, value: str, confidence: float, margin: float | None) -> Decision:
    return Decision(
        point_id=point_id, stage=Stage.PRE, mode=Mode.ACTIVE,
        value=value, confidence=confidence, margin=margin,
    )


class TestRouteMarginAndBudget:
    def test_margin_gate_forces_full(self):
        engine = CascadeEngine(registry=None)
        pre = [
            _decision("intent", "simple", 0.9, margin=0.05),
            _decision("needs_reasoning", "no_reasoning", 0.9, margin=0.5),
            _decision("complexity", "low", 0.9, margin=0.5),
            _decision("safety", "safe", 0.9, margin=0.5),
        ]
        assert engine.route_chat(pre, margin_tau=0.1) is Route.FULL

    def test_margin_disabled_keeps_legacy_route(self):
        engine = CascadeEngine(registry=None)
        pre = [
            _decision("intent", "simple", 0.9, margin=0.01),
            _decision("needs_reasoning", "no_reasoning", 0.9, margin=0.01),
            _decision("complexity", "low", 0.9, margin=0.01),
            _decision("safety", "safe", 0.9, margin=0.01),
        ]
        assert engine.route_chat(pre) is Route.LIGHT

    def test_budget_blocks_full(self):
        engine = CascadeEngine(registry=None)
        pre = [
            _decision("intent", "complex", 0.9, margin=0.5),
            _decision("needs_reasoning", "reasoning", 0.9, margin=0.5),
            _decision("safety", "safe", 0.9, margin=0.5),
        ]
        tight = BudgetContext(max_brain_tokens=10, state_tokens=1000)
        assert engine.route_chat(pre, budget=tight) is Route.LIGHT

    def test_budget_allows_full_when_ample(self):
        engine = CascadeEngine(registry=None)
        pre = [
            _decision("intent", "complex", 0.9, margin=0.5),
            _decision("needs_reasoning", "reasoning", 0.9, margin=0.5),
            _decision("safety", "safe", 0.9, margin=0.5),
        ]
        ample = BudgetContext(max_brain_tokens=100000, state_tokens=10)
        assert engine.route_chat(pre, budget=ample) is Route.FULL

    def test_budget_estimates(self):
        budget = BudgetContext(max_brain_tokens=1000, state_tokens=100, completion_tokens=256)
        assert budget.estimated_brain_tokens() == 110 + 256  # ceil(1.1×100)=110
        assert budget.allows_full()
        assert budget.effective_max_tokens(512) == 512
        tiny = BudgetContext(max_brain_tokens=200, state_tokens=100, completion_tokens=256)
        assert not tiny.allows_full()
        assert tiny.effective_max_tokens(512) == 90
