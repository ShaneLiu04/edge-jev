"""AR005 T008/T009：矩阵配置、汇总与风险校准（无后端纯逻辑）。"""

import json

from jev.bench.matrix import (
    _ARM_OVERLAYS,
    _arm_config,
    _calibrate_risk,
    _deep_merge,
    _render_report,
    _summarize,
    load_matrix_config,
)


def test_load_matrix_config_defaults():
    config = load_matrix_config("configs/experiments/ar005.toml")
    assert config.name == "ar005-local-1.5b"
    assert config.arms == ("B0", "B1", "B2", "AR005")
    assert config.seed == 20261026
    assert config.brain == "spawn"


def test_arm_overlays_and_deep_merge():
    base = {"runtime": {"enabled": False, "tau_low": 0.35}, "cache": {"enabled": True}}
    merged = _deep_merge(base, _ARM_OVERLAYS["B0"])
    assert merged["runtime"]["enabled"] is False
    assert merged["runtime"]["tau_low"] == 0.35
    assert merged["cache"]["enabled"] is False
    ar = _arm_config(base, "AR005")
    assert ar["runtime"]["risk_control"] is True
    assert ar["cache"]["conformal_admission"] is True


def test_summarize_and_report():
    raw = {
        "B2": [
            {
                "question_id": "q1",
                "quality": 1.0,
                "completion_tokens": 10,
                "prompt_tokens": 5,
                "latency_ms": 100.0,
                "route": "light",
                "answer": "a1",
                "headers": {"x-jev-cache": "miss", "x-jev-risk": "off"},
            },
            {
                "question_id": "q2",
                "quality": 0.0,
                "completion_tokens": 20,
                "prompt_tokens": 5,
                "latency_ms": 200.0,
                "route": "full",
                "answer": "a2",
                "headers": {"x-jev-cache": "hit-exact", "x-jev-risk": "deny"},
            },
        ]
    }
    metrics = _summarize(raw)
    assert metrics["B2"]["quality"] == 0.5
    assert metrics["B2"]["cache_hits"] == 1
    assert metrics["B2"]["risk_denies"] == 1
    report = _render_report(
        {
            "run": {"name": "r", "lambda_cost": 0.1, "seed": 1, "limit": 0, "fingerprint": {}},
            "metrics": metrics,
            "selection": {
                "decision": "rollback",
                "chosen_arm": "B2",
                "baseline_arm": "B2",
                "delta_ci": None,
                "reason": "无增益",
            },
            "calibrations": {},
        }
    )
    assert "rollback" in report
    assert "B2" in report


def test_calibrate_risk_writes_only_when_feasible(tmp_path):
    b0 = [
        {"question_id": f"q{i}", "quality": 1.0, "u": None} for i in range(25)
    ]
    b2 = [
        {"question_id": f"q{i}", "quality": 0.0, "u": 0.1 + (i % 5) * 0.05}
        for i in range(25)
    ]
    assert _calibrate_risk(b0, b2, tmp_path, 0.1) is None
    for row in b2:
        row["quality"] = 1.0
    result = _calibrate_risk(b0, b2, tmp_path, 0.1)
    assert result is not None
    data = json.loads((tmp_path / "risk.json").read_text(encoding="utf-8"))
    assert data["enabled"] is True
    assert "threshold" in data


def test_combo_arms_require_overlay_definition(tmp_path):
    import pytest

    from jev.errors import JevError

    good = tmp_path / "good.toml"
    good.write_text(
        'name = "x"\narms = ["B1", "AEB"]\n'
        '[overlays.AEB.prefill]\nenabled = true\nevidence = true\n',
        encoding="utf-8",
    )
    config = load_matrix_config(good)
    assert config.arms == ("B1", "AEB")
    assert config.overlays["AEB"]["prefill"]["evidence"] is True

    bad = tmp_path / "bad.toml"
    bad.write_text('name = "x"\narms = ["NOPE"]\n', encoding="utf-8")
    with pytest.raises(JevError):
        load_matrix_config(bad)


def test_combo_arm_config_is_base_plus_overlay():
    base = {
        "runtime": {"enabled": False, "allocator_enabled": False},
        "prefill": {"enabled": False, "budget_ratio": 0.6},
    }
    extra = {
        "runtime": {"enabled": True, "allocator_enabled": True},
        "prefill": {"enabled": True, "evidence": True, "adaptive": True},
    }
    merged = _arm_config(base, "AEB_ALLOC", extra)
    assert merged["runtime"]["enabled"] is True
    assert merged["prefill"]["evidence"] is True
    assert merged["prefill"]["budget_ratio"] == 0.6
