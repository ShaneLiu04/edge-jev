"""AR003 T001–T003：不确定性 / 自证 / 三带（纯逻辑，FR1-1/FR1-2）。"""

import math

import pytest

from jev.kernel.decisions.builtin import load_builtin_registry
from jev.kernel.decisions.framework import Decision, Mode
from jev.kernel.decisions.probes import FusedPoint
from jev.kernel.runtime.bands import BandPolicy, classify_band
from jev.kernel.runtime.selfcheck import self_agreement
from jev.kernel.runtime.uncertainty import (
    Uncertainty,
    UncertaintyWeights,
    estimate_uncertainty,
    js_divergence,
)

_REGISTRY = load_builtin_registry()


def _decision(point_id: str, value: str, confidence: float, *, margin=None) -> Decision:
    point = _REGISTRY.get(point_id)
    return Decision(
        point_id=point_id,
        stage=point.stage,
        mode=Mode.ACTIVE,
        value=value,
        confidence=confidence,
        margin=margin,
    )


def _fused(point_id: str, distribution, *, probes=None, margin=None) -> FusedPoint:
    top = max(distribution, key=distribution.__getitem__)
    return FusedPoint(
        point_id=point_id,
        distribution=distribution,
        confidence=distribution[top],
        margin=margin if margin is not None else 0.5,
        probes=probes or {},
    )


class TestJsDivergence:
    def test_identical_distributions_is_zero(self):
        assert js_divergence({"a": 0.5, "b": 0.5}, {"a": 0.5, "b": 0.5}) == 0.0

    def test_disjoint_distributions_is_one(self):
        assert js_divergence({"a": 1.0}, {"b": 1.0}) == pytest.approx(1.0)

    def test_symmetric_and_bounded(self):
        p = {"a": 0.9, "b": 0.1}
        q = {"a": 0.2, "b": 0.8}
        assert js_divergence(p, q) == pytest.approx(js_divergence(q, p))
        assert 0.0 <= js_divergence(p, q) <= 1.0

    def test_handles_missing_labels(self):
        assert js_divergence({"a": 1.0}, {"a": 0.5, "b": 0.5}) > 0.0


class TestEstimateUncertainty:
    def test_all_optimal_is_zero(self):
        decisions = [
            _decision("intent", "decision", 1.0, margin=1.0),
            _decision("safety", "safe", 1.0, margin=1.0),
        ]
        fused = {
            "intent": _fused("intent", {"decision": 1.0}, probes={"handling": {"decision": 1.0}}),
            "safety": _fused("safety", {"safe": 1.0}, probes={"p": {"safe": 1.0}}),
        }
        result = estimate_uncertainty(decisions, fused)
        assert result.u == pytest.approx(0.0)
        assert result.self_agree == pytest.approx(1.0)
        assert result.total_margin == pytest.approx(1.0)

    def test_all_worst_is_zero_boundary(self):
        decisions = [_decision("intent", "complex", 0.0, margin=0.0)]
        fused = {
            "intent": _fused(
                "intent",
                {"complex": 0.5, "simple": 0.5},
                probes={"a": {"complex": 1.0}, "b": {"simple": 1.0}},
                margin=0.0,
            )
        }
        result = estimate_uncertainty(decisions, fused)
        assert 0.0 <= result.u <= 1.0
        assert result.u > 0.6

    def test_u_within_unit_interval_and_deterministic(self):
        decisions = [_decision("intent", "complex", 0.55, margin=0.2)]
        fused = {
            "intent": _fused(
                "intent",
                {"complex": 0.6, "simple": 0.4},
                probes={"a": {"complex": 0.7, "simple": 0.3}, "b": {"complex": 0.5, "simple": 0.5}},
                margin=0.2,
            )
        }
        first = estimate_uncertainty(decisions, fused)
        second = estimate_uncertainty(decisions, fused)
        assert first == second
        assert 0.0 <= first.u <= 1.0

    def test_monotonic_in_confidence(self):
        fused = {"intent": _fused("intent", {"complex": 1.0}, probes={"a": {"complex": 1.0}})}
        high = estimate_uncertainty([_decision("intent", "complex", 0.9, margin=0.9)], fused)
        low = estimate_uncertainty([_decision("intent", "complex", 0.3, margin=0.9)], fused)
        assert low.u > high.u

    def test_missing_margin_renormalizes(self):
        decisions = [_decision("intent", "complex", 0.8)]
        fused = {"intent": _fused("intent", {"complex": 0.8, "simple": 0.2})}
        result = estimate_uncertainty(decisions, fused)
        assert result.total_margin is None
        assert 0.0 <= result.u <= 1.0

    def test_missing_self_agreement_equals_full_agreement(self):
        decisions = [_decision("intent", "complex", 0.8, margin=0.6)]
        with_probe = {
            "intent": _fused(
                "intent",
                {"complex": 0.8, "simple": 0.2},
                probes={"a": {"complex": 0.8, "simple": 0.2}},
            )
        }
        no_fused = {}
        result_probe = estimate_uncertainty(decisions, with_probe)
        result_empty = estimate_uncertainty(decisions, no_fused)
        # 单探针一致率为 1；缺失亦置 1 → 其余项相同
        assert result_probe.self_agree == 1.0
        assert result_empty.self_agree == 1.0
        assert result_probe.u == pytest.approx(result_empty.u)

    def test_empty_decisions_is_most_uncertain(self):
        result = estimate_uncertainty([], {})
        assert isinstance(result, Uncertainty)
        assert result.u == 1.0

    def test_zero_weights_rejected(self):
        with pytest.raises(ValueError):
            estimate_uncertainty([], {}, weights=UncertaintyWeights(0.0, 0.0, 0.0, 0.0))

    def test_negative_weight_rejected(self):
        with pytest.raises(ValueError):
            UncertaintyWeights(confidence=-0.1)

    def test_ignores_shadow_decisions(self):
        active = _decision("intent", "complex", 0.9, margin=0.9)
        shadow = Decision(
            point_id="complexity",
            stage=_REGISTRY.get("complexity").stage,
            mode=Mode.SHADOW,
            value="high",
            confidence=0.0,
        )
        fused = {"intent": _fused("intent", {"complex": 1.0}, probes={"a": {"complex": 1.0}})}
        with_shadow = estimate_uncertainty([active, shadow], fused)
        without = estimate_uncertainty([active], fused)
        assert with_shadow == without


class TestSelfAgreement:
    def test_no_probes_is_perfect(self):
        fused = {"intent": _fused("intent", {"complex": 1.0})}
        evidence = self_agreement(fused)
        assert evidence.consistency == 1.0
        assert evidence.cross_point == 1.0

    def test_unanimous_probes(self):
        fused = {
            "intent": _fused(
                "intent",
                {"complex": 1.0},
                probes={"a": {"complex": 1.0}, "b": {"complex": 1.0}},
            )
        }
        assert self_agreement(fused).consistency == pytest.approx(1.0)

    def test_split_probes_lower_consistency(self):
        fused = {
            "intent": _fused(
                "intent",
                {"complex": 0.5, "simple": 0.5},
                probes={"a": {"complex": 1.0}, "b": {"simple": 1.0}},
            )
        }
        evidence = self_agreement(fused)
        assert evidence.consistency == pytest.approx(0.5)
        assert evidence.cross_point == 0.0

    def test_deterministic(self):
        fused = {
            "intent": _fused(
                "intent",
                {"complex": 0.6, "simple": 0.4},
                probes={"a": {"complex": 0.7, "simple": 0.3}},
            )
        }
        assert self_agreement(fused) == self_agreement(fused)


class TestBands:
    def test_boundaries(self):
        policy = BandPolicy(tau_low=0.35, tau_high=0.65)
        assert classify_band(0.0, policy) == "confident"
        assert classify_band(0.35, policy) == "confident"
        assert classify_band(0.3500001, policy) == "borderline"
        assert classify_band(0.64, policy) == "borderline"
        assert classify_band(0.65, policy) == "uncertain"
        assert classify_band(1.0, policy) == "uncertain"

    def test_nan_is_uncertain(self):
        assert classify_band(math.nan, BandPolicy()) == "uncertain"

    def test_out_of_range_u_clamped(self):
        assert classify_band(-0.5, BandPolicy()) == "confident"
        assert classify_band(2.0, BandPolicy()) == "uncertain"

    def test_invalid_policy_rejected(self):
        with pytest.raises(ValueError):
            BandPolicy(tau_low=0.7, tau_high=0.6)
        with pytest.raises(ValueError):
            BandPolicy(tau_low=-0.1)
        with pytest.raises(ValueError):
            BandPolicy(tau_high=1.5)

    def test_gate_threshold_validated(self):
        with pytest.raises(ValueError):
            BandPolicy(gate_threshold=1.2)
        with pytest.raises(ValueError):
            BandPolicy(self_verify_mode="unknown")
