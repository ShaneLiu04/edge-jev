"""AR005 T001/T003：Conformal 风险控制与缓存准入（FR5-1/FR5-2）。"""

import json

import pytest

from jev.errors import JevProtocolError
from jev.kernel.runtime.conformal import (
    AdmissionPolicy,
    RiskController,
    calibrate_cache_admission,
    conformal_risk_threshold,
)


def _samples(n: int, safe_fraction: float, loss_safe: float = 0.0, loss_risky: float = 1.0):
    safe = round(n * safe_fraction)
    scores = [0.1] * safe + [0.9] * (n - safe)
    losses = [loss_safe] * safe + [loss_risky] * (n - safe)
    return scores, losses


def test_le_direction_admits_safe_scores_with_crc_bound():
    scores, losses = _samples(40, 0.6)
    result = conformal_risk_threshold(scores, losses, alpha=0.1, direction="le")
    assert result.enabled is True
    assert result.threshold == pytest.approx(0.1)
    assert result.coverage == pytest.approx(0.6)
    assert result.bound <= 0.1
    assert result.risk == 0.0


def test_le_direction_refuses_when_loss_too_high():
    scores, losses = _samples(40, 0.9, loss_safe=0.5)
    result = conformal_risk_threshold(scores, losses, alpha=0.1, direction="le")
    assert result.enabled is False


def test_ge_direction_admits_high_scores():
    scores = [0.95] * 10 + [0.2] * 30
    losses = [0.0] * 10 + [1.0] * 30
    result = conformal_risk_threshold(scores, losses, alpha=0.1, direction="ge")
    assert result.enabled is True
    assert result.threshold == pytest.approx(0.95)
    assert result.coverage == pytest.approx(0.25)


def test_min_samples_disables():
    result = conformal_risk_threshold([0.1] * 5, [0.0] * 5, alpha=0.1)
    assert result.enabled is False
    assert result.threshold is None


def test_bad_inputs_rejected():
    with pytest.raises(ValueError):
        conformal_risk_threshold([0.1], [0.0, 0.1], alpha=0.1)
    with pytest.raises(ValueError):
        conformal_risk_threshold([0.1] * 20, [0.0] * 20, alpha=0.0)
    with pytest.raises(ValueError):
        conformal_risk_threshold([0.1] * 20, [0.0] * 20, alpha=0.1, direction="xx")
    with pytest.raises(ValueError):
        conformal_risk_threshold([float("nan")] * 20, [0.0] * 20, alpha=0.1)


def test_risk_controller_monotone_and_disabled():
    scores, losses = _samples(40, 0.5)
    result = conformal_risk_threshold(scores, losses, alpha=0.1)
    controller = RiskController(result)
    assert controller.allow_downgrade(0.0) is True
    assert controller.allow_downgrade(0.9) is False
    assert controller.allow_downgrade(float("nan")) is False
    off = RiskController.disabled()
    assert off.enabled is False
    assert off.allow_downgrade(0.99) is True


def test_from_file_roundtrip_and_invalid(tmp_path):
    scores, losses = _samples(40, 0.5)
    result = conformal_risk_threshold(scores, losses, alpha=0.1)
    path = tmp_path / "risk.json"
    path.write_text(json.dumps(result.stats()), encoding="utf-8")
    controller = RiskController.from_file(path)
    assert controller.enabled is True
    assert controller.threshold == pytest.approx(result.threshold)

    bad = tmp_path / "bad.json"
    bad.write_text("{not-json", encoding="utf-8")
    with pytest.raises(JevProtocolError):
        RiskController.from_file(bad)
    tiny = tmp_path / "tiny.json"
    tiny.write_text(json.dumps({"threshold": 0.5, "n": 3}), encoding="utf-8")
    with pytest.raises(JevProtocolError):
        RiskController.from_file(tiny)
    with pytest.raises(JevProtocolError):
        RiskController.from_file(tmp_path / "missing.json")


def test_cache_admission_controls_false_hits_and_recall():
    positives = [0.95, 0.94, 0.96, 0.93, 0.95, 0.92, 0.94, 0.9, 0.97, 0.91]
    negatives = [0.5, 0.4, 0.45, 0.3, 0.2, 0.35, 0.25, 0.55, 0.15, 0.6,
                 0.42, 0.33, 0.28, 0.48, 0.38, 0.22, 0.31, 0.44, 0.26, 0.36]
    policy = calibrate_cache_admission(positives, negatives, alpha=0.1, margin=0.02)
    assert policy.enabled is True
    assert policy.threshold is not None and policy.threshold <= 0.6
    false_hits = sum(1 for score in negatives if score >= policy.threshold)
    assert false_hits <= 1
    assert policy.recall >= 0.6
    assert policy.allows(0.95, 0.5) is True
    assert policy.allows(0.95, 0.94) is False


def test_cache_admission_disables_when_recall_too_low():
    positives = [0.1] * 10
    negatives = [0.9] * 20
    policy = calibrate_cache_admission(positives, negatives, alpha=0.1, min_recall=0.6)
    assert policy.enabled is False
    assert policy.allows(0.99, 0.0) is False


def test_cache_admission_disabled_policy_allows_nothing():
    policy = AdmissionPolicy(False, None, 0.1, 0.02, 0.0, 0)
    assert policy.allows(1.0, 0.0) is False
