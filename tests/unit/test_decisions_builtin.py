"""T008：内置 18 决策点测试——清单/分布/校验 + 与 configs/decision-points.toml 同步。

对应实现契约 src/jev/kernel/decisions/builtin.py：BUILTIN_POINTS 18 点常量表
（PRE9/MID4/POST5，与 configs/decision-points.toml 同名同序）+ load_builtin_registry()。
TOML 同步用例对照仓库真实 configs/decision-points.toml（configs_dir fixture）。
"""

from jev.kernel.decisions.builtin import BUILTIN_POINTS, load_builtin_registry
from jev.kernel.decisions.framework import DecisionRegistry, Mode, Stage

PRE_IDS = [
    "needs_generation",
    "needs_reasoning",
    "intent",
    "complexity",
    "ambiguity",
    "safety",
    "verbosity",
    "format",
    "needs_tool",
]
MID_IDS = ["on_track", "step_complete", "should_stop", "confidence_mid"]
POST_IDS = ["answer_complete", "matches_request", "needs_retry", "too_verbose", "correct_format"]
EXPECTED_IDS = PRE_IDS + MID_IDS + POST_IDS


def test_builtin_points_contains_exactly_18_points():
    assert len(BUILTIN_POINTS) == 18


def test_builtin_ids_cover_srs_fr4_listing_point_by_point():
    """srs.md §3.4 FR4 点名清单逐一举证（子集断言；数量=18 保证无多余点）。"""
    ids = {point.id for point in BUILTIN_POINTS}
    for expected in EXPECTED_IDS:
        assert expected in ids


def test_builtin_ids_follow_toml_declaration_order():
    assert [point.id for point in BUILTIN_POINTS] == EXPECTED_IDS


def test_builtin_stage_distribution_is_nine_four_five():
    stages = [point.stage for point in BUILTIN_POINTS]
    assert stages.count(Stage.PRE) == 9
    assert stages.count(Stage.MID) == 4
    assert stages.count(Stage.POST) == 5


def test_builtin_points_declare_valid_tau():
    for point in BUILTIN_POINTS:
        assert 0 < point.tau <= 1, point.id


def test_builtin_choice_and_score_points_declare_valid_options():
    for point in BUILTIN_POINTS:
        if point.question_type in ("choice", "score"):
            assert len(point.options) >= 2, point.id
            assert point.fallback_action in point.options, point.id


def test_load_builtin_registry_matches_builtin_points():
    registry = load_builtin_registry()
    assert list(registry.ids) == EXPECTED_IDS
    for point in BUILTIN_POINTS:
        assert registry.get(point.id) == point


def test_safety_is_active_and_ambiguity_is_shadow():
    registry = load_builtin_registry()
    assert registry.get("safety").mode is Mode.ACTIVE
    assert registry.get("ambiguity").mode is Mode.SHADOW


def test_toml_registry_matches_builtin_registry(configs_dir):
    """声明式 TOML 与内置常量表同步：id 序列与每点全字段一致。"""
    toml_registry = DecisionRegistry.from_toml(configs_dir / "decision-points.toml")
    builtin_registry = load_builtin_registry()

    assert list(toml_registry.ids) == list(builtin_registry.ids)
    for point_id in builtin_registry.ids:
        assert toml_registry.get(point_id) == builtin_registry.get(point_id)
