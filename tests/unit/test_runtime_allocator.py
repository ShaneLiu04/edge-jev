"""AR005 T005/T007：JCBA 分配器与 profile 加载（FR5-3/FR5-4）。"""

import pytest

from jev.errors import JevProtocolError
from jev.kernel.runtime.allocator import AllocatorConfig, Unit, allocate
from jev.kernel.runtime.portfolio import load_profile, merge_profile, to_toml


def _config(**overrides):
    defaults = dict(enabled=True, budget_ratio=0.5, min_prompt_tokens=10, min_blocks=2)
    defaults.update(overrides)
    return AllocatorConfig(**defaults)


def test_disabled_keeps_everything():
    units = [Unit("a", 100, 0.5, 0), Unit("b", 100, 0.5, 1)]
    result = allocate(units, config=AllocatorConfig())
    assert result.skipped == "disabled"
    assert result.selected == ("a", "b")
    assert result.saved_tokens == 0


def test_short_prompt_skips():
    units = [Unit("a", 5, 0.5, 0), Unit("b", 5, 0.5, 1)]
    result = allocate(units, config=_config(min_prompt_tokens=100))
    assert result.skipped == "short"


def test_mandatory_over_budget_skips():
    units = [
        Unit("sys", 90, 10.0, 0, mandatory=True),
        Unit("last", 30, 10.0, 1, mandatory=True),
        Unit("opt", 20, 0.5, 2),
    ]
    result = allocate(units, config=_config(budget_ratio=0.5, min_blocks=1))
    assert result.skipped == "mandatory_over_budget"
    assert result.after_tokens == result.before_tokens


def test_picks_high_benefit_units_under_budget():
    units = [
        Unit("sys", 100, 10.0, 0, mandatory=True),
        Unit("recent", 100, 1.0, 1, mandatory=True),
        Unit("a", 100, 0.9, 2),
        Unit("b", 100, 0.1, 3),
        Unit("c", 100, 0.8, 4),
    ]
    result = allocate(units, config=_config(budget_ratio=0.8))
    assert result.skipped is None
    assert result.selected == ("sys", "recent", "a", "c")
    assert result.omitted == ("b",)
    assert result.after_tokens == 400
    assert result.before_tokens == 500
    assert result.saved_tokens == 100


def test_deterministic_across_calls():
    units = [
        Unit("sys", 100, 10.0, 0, mandatory=True),
        Unit("a", 40, 0.4, 1),
        Unit("b", 40, 0.4, 2),
        Unit("c", 40, 0.4, 3),
    ]
    first = allocate(units, config=_config(budget_ratio=0.99))
    second = allocate(list(reversed(units)), config=_config(budget_ratio=0.99))
    assert first == second
    assert len(first.selected) == 3
    assert first.selected[0] == "sys"


def test_few_units_skips():
    units = [Unit("sys", 100, 10.0, 0, mandatory=True), Unit("a", 100, 0.5, 1)]
    result = allocate(units, config=_config(min_blocks=3))
    assert result.skipped == "few_units"


def test_no_gain_returns_all():
    units = [
        Unit("sys", 100, 10.0, 0, mandatory=True),
        Unit("zero", 0, 0.0, 1),
    ]
    result = allocate(units, config=_config(budget_ratio=1.0, min_blocks=1))
    assert result.skipped == "no_gain"
    assert result.selected == ("sys", "zero")


def test_invalid_config_and_unit():
    with pytest.raises(ValueError):
        AllocatorConfig(budget_ratio=0)
    with pytest.raises(ValueError):
        AllocatorConfig(min_blocks=0)
    with pytest.raises(ValueError):
        Unit("a", -1, 0.5, 0)


def test_profile_roundtrip(tmp_path):
    overlay = {
        "portfolio": {"name": "AR005"},
        "runtime": {"risk_control": True, "allocator_enabled": True},
        "cache": {"conformal_admission": True},
    }
    text = to_toml("AR005", overlay)
    path = tmp_path / "profile.toml"
    path.write_text(text, encoding="utf-8")
    profile = load_profile(path)
    assert profile.name == "AR005"
    assert profile.get("runtime", "risk_control") is True
    merged = merge_profile({"runtime": {"enabled": False}}, profile)
    assert merged["runtime"]["enabled"] is False
    assert merged["runtime"]["risk_control"] is True
    assert merged["cache"]["conformal_admission"] is True


def test_profile_rejects_unknown_or_missing(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text("[weird]\nx = 1\n", encoding="utf-8")
    with pytest.raises(JevProtocolError):
        load_profile(path)
    with pytest.raises(JevProtocolError):
        load_profile(tmp_path / "missing.toml")
    path.write_text("not = [", encoding="utf-8")
    with pytest.raises(JevProtocolError):
        load_profile(path)
