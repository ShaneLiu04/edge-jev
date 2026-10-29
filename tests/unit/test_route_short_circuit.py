"""AR012 路由短路纯逻辑测试（无后端）。"""

from jev.kernel.cascade.engine import CascadeEngine, Route
from jev.kernel.decisions.framework import (
    Decision,
    DecisionPoint,
    DecisionRegistry,
    Mode,
    Stage,
)


def _point(pid: str, options: tuple[str, ...], fallback: str) -> DecisionPoint:
    return DecisionPoint(
        id=pid,
        stage=Stage.PRE,
        question=pid,
        tau=0.6,
        fallback_action=fallback,
        question_type="choice",
        options=options,
    )


def _decision(point: DecisionPoint, value: str) -> Decision:
    return Decision(
        point_id=point.id,
        stage=point.stage,
        mode=Mode.ACTIVE,
        value=value,
        confidence=0.9,
        margin=0.5,
    )


class _Budget:
    def __init__(self, full: bool) -> None:
        self._full = full

    def allows_full(self) -> bool:
        return self._full


def _engine():
    registry = DecisionRegistry()
    points = {
        "safety": _point("safety", ("unsafe", "safe"), "safe"),
        "intent": _point("intent", ("decision", "complex"), "complex"),
        "needs_reasoning": _point(
            "needs_reasoning", ("reasoning", "no_reasoning"), "reasoning"
        ),
    }
    for point in points.values():
        registry.register(point)
    return CascadeEngine(registry), points


def _pre(points, **values):
    return [_decision(points[pid], value) for pid, value in values.items()]


def test_unsafe_short_circuits_full():
    engine, points = _engine()
    pre = _pre(points, safety="unsafe", intent="complex", needs_reasoning="no_reasoning")
    assert engine.route_short_circuit(pre) is Route.FULL


def test_decision_intent_short_circuits_direct():
    engine, points = _engine()
    pre = _pre(points, safety="safe", intent="decision", needs_reasoning="reasoning")
    assert engine.route_short_circuit(pre) is Route.DIRECT


def test_reasoning_short_circuits_full():
    engine, points = _engine()
    pre = _pre(points, safety="safe", intent="complex", needs_reasoning="reasoning")
    assert engine.route_short_circuit(pre) is Route.FULL


def test_no_reasoning_needs_more_decisions():
    engine, points = _engine()
    pre = _pre(points, safety="safe", intent="complex", needs_reasoning="no_reasoning")
    assert engine.route_short_circuit(pre) is None


def test_budget_exhausted_short_circuits_light():
    engine, points = _engine()
    pre = _pre(points, safety="safe", intent="complex", needs_reasoning="no_reasoning")
    assert engine.route_short_circuit(pre, budget=_Budget(False)) is Route.LIGHT


def test_fallback_short_circuits_full():
    engine, points = _engine()
    pre = _pre(points, safety="safe", intent="complex", needs_reasoning="reasoning")
    pre[0].from_fallback = True
    assert engine.route_short_circuit(pre) is Route.FULL
