"""AR013 · RDMP 纯逻辑：最小探针与确认判定。"""

from jev.kernel.cascade.engine import Route, needs_minimal_confirmation
from jev.kernel.decisions.framework import DecisionPoint, Stage, minimize_points
from jev.kernel.runtime.config import RuntimeConfig


def _point(pid: str, *, permutations: int, probes: tuple = ()) -> DecisionPoint:
    return DecisionPoint(
        id=pid,
        stage=Stage.PRE,
        question=pid,
        tau=0.6,
        fallback_action="safe",
        question_type="choice",
        options=("a", "b"),
        permutations=permutations,
        probes=probes,
    )


def test_minimize_points_forces_single_probe_and_permutation():
    points = [_point("a", permutations=4, probes=("p1", "p2")), _point("b", permutations=1)]
    minimized = minimize_points(points)
    assert [(p.id, p.permutations, p.probes) for p in minimized] == [
        ("a", 1, ()),
        ("b", 1, ()),
    ]


def test_minimize_points_keeps_other_fields():
    point = _point("safety", permutations=2, probes=("x",))
    minimized = minimize_points([point])[0]
    assert minimized.stage is point.stage
    assert minimized.options == point.options
    assert minimized.tau == point.tau


def test_full_route_does_not_require_confirmation():
    assert needs_minimal_confirmation(Route.FULL) is False


def test_cheap_or_unknown_routes_require_confirmation():
    assert needs_minimal_confirmation(Route.DIRECT) is True
    assert needs_minimal_confirmation(Route.LIGHT) is True
    assert needs_minimal_confirmation(None) is True


def test_minimal_switch_is_off_by_default():
    assert RuntimeConfig().decision_staged_minimal is False
    assert RuntimeConfig(decision_staged_minimal=True).decision_staged_minimal is True
