"""AR003 T002：三带路由 v2 与 AR002 逐例回归（FR1-2/FR1-4）。"""

import pytest

from jev.kernel.cascade.budget import BudgetContext
from jev.kernel.cascade.engine import CascadeEngine, Route, RouteDecision
from jev.kernel.decisions.builtin import load_builtin_registry
from jev.kernel.decisions.framework import Decision, Mode
from jev.kernel.runtime.bands import BandPolicy
from jev.kernel.runtime.uncertainty import Uncertainty

_REGISTRY = load_builtin_registry()


def _d(point_id, value, confidence, *, mode=Mode.ACTIVE, margin=None, fallback=False):
    point = _REGISTRY.get(point_id)
    return Decision(
        point_id=point_id,
        stage=point.stage,
        mode=mode,
        value=value,
        confidence=confidence,
        margin=margin,
        from_fallback=fallback,
    )


def _pre(**overrides):
    defaults = {
        "needs_generation": ("generation", 0.9),
        "needs_reasoning": ("no_reasoning", 0.9),
        "intent": ("simple", 0.9),
        "complexity": ("low", 0.9),
        "safety": ("safe", 0.9),
    }
    batch = []
    for point_id, (value, confidence) in defaults.items():
        spec = overrides.get(point_id, (value, confidence))
        if spec is None:
            continue
        if isinstance(spec, Decision):
            batch.append(spec)
        else:
            batch.append(_d(point_id, spec[0], spec[1]))
    return batch


def _u(value: float, *, self_agree: float = 1.0) -> Uncertainty:
    return Uncertainty(
        u=value,
        total_margin=None,
        disagreement=None,
        self_agree=self_agree,
        probe_agreement=self_agree,
    )


@pytest.fixture
def engine():
    return CascadeEngine(load_builtin_registry())


class TestEquivalenceWithAr002:
    """uncertainty=None 必须与 route_chat 逐例一致（默认行为回归）。"""

    CORPUS = [
        {},
        {"intent": ("decision", 0.9)},
        {"intent": ("decision", 0.5)},
        {"needs_reasoning": ("reasoning", 0.9)},
        {"needs_generation": ("no_generation", 0.9)},
        {"complexity": ("medium", 0.9)},
        {"safety": ("unsafe", 0.9)},
        {"safety": ("safe", 0.5)},
        {"intent": None},
    ]

    @pytest.mark.parametrize("overrides", CORPUS)
    def test_same_route(self, engine, overrides):
        pre = _pre(**overrides)
        legacy = engine.route_chat(pre)
        v2 = engine.route_chat_v2(pre, uncertainty=None)
        assert isinstance(v2, RouteDecision)
        assert v2.route is legacy
        assert v2.needs_verify is False
        assert v2.band == "off"

    def test_budget_and_margin_passthrough(self, engine):
        pre = [_d("intent", "complex", 0.9, margin=0.05), _d("safety", "safe", 0.9)]
        tight = BudgetContext(max_brain_tokens=1, state_tokens=1000)
        assert engine.route_chat_v2(pre, margin_tau=0.1).route is Route.FULL
        assert engine.route_chat_v2(pre, budget=tight).route is Route.LIGHT


class TestBands:
    def test_confident_delegates_to_capability_gate(self, engine):
        # 能力门控：no_generation + no_reasoning + low → direct
        pre = _pre(needs_generation=("no_generation", 0.9))
        result = engine.route_chat_v2(pre, uncertainty=_u(0.10))
        assert result.route is Route.DIRECT
        assert result.band == "confident"
        assert result.needs_verify is False

    def test_uncertain_forces_full_even_if_gate_would_be_direct(self, engine):
        pre = _pre(needs_generation=("no_generation", 0.9))
        result = engine.route_chat_v2(pre, uncertainty=_u(0.90))
        assert result.route is Route.FULL
        assert result.band == "uncertain"
        assert result.needs_verify is False

    def test_borderline_verifier_disabled_matches_ar002(self, engine):
        pre = _pre()
        result = engine.route_chat_v2(
            pre, uncertainty=_u(0.50), policy=BandPolicy(verifier_enabled=False)
        )
        assert result.route is engine.route_chat(pre)
        assert result.needs_verify is False

    def test_borderline_verifier_enabled_needs_verify(self, engine):
        pre = _pre()
        result = engine.route_chat_v2(
            pre,
            uncertainty=_u(0.50),
            policy=BandPolicy(verifier_enabled=True),
        )
        assert result.route is Route.LIGHT
        assert result.needs_verify is True

    def test_borderline_budget_insufficient_skips_verification(self, engine):
        pre = _pre()
        tight = BudgetContext(max_brain_tokens=1, state_tokens=1000)
        result = engine.route_chat_v2(
            pre,
            uncertainty=_u(0.50),
            policy=BandPolicy(verifier_enabled=True),
            budget=tight,
        )
        assert result.needs_verify is False
        assert result.skipped_reason == "budget"
        assert result.route is Route.LIGHT

    def test_self_verify_gate_promotes_band(self, engine):
        pre = _pre(needs_generation=("no_generation", 0.9))
        result = engine.route_chat_v2(
            pre,
            uncertainty=_u(0.10, self_agree=0.30),
            policy=BandPolicy(verifier_enabled=True, self_verify_mode="gate"),
        )
        assert result.band == "uncertain"
        assert result.route is Route.FULL

    def test_self_verify_observe_does_not_change_route(self, engine):
        pre = _pre(needs_generation=("no_generation", 0.9))
        observe = engine.route_chat_v2(
            pre,
            uncertainty=_u(0.10, self_agree=0.10),
            policy=BandPolicy(verifier_enabled=True, self_verify_mode="observe"),
        )
        baseline = engine.route_chat(pre)
        assert observe.route is baseline
        assert observe.band == "confident"


class TestHardSignals:
    def test_from_fallback_forces_full_without_verify(self, engine):
        pre = _pre(complexity=_d("complexity", "low", 0.9, fallback=True))
        result = engine.route_chat_v2(
            pre,
            uncertainty=_u(0.90),
            policy=BandPolicy(verifier_enabled=True),
        )
        assert result.route is Route.FULL
        assert result.needs_verify is False
        assert result.band == "uncertain"

    def test_unsafe_forces_full_without_verify(self, engine):
        pre = _pre(safety=("unsafe", 0.9))
        result = engine.route_chat_v2(
            pre,
            uncertainty=_u(0.10),
            policy=BandPolicy(verifier_enabled=True),
        )
        assert result.route is Route.FULL
        assert result.needs_verify is False
