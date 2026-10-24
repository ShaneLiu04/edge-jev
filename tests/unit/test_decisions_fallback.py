"""T008：决策点安全侧回退测试——fallback_point 生成低置信回退决策。

对应实现契约 src/jev/kernel/decisions/fallback.py：小脑不可用/熔断时，
决策点回退到 fallback_action（confidence=0.0，from_fallback=True，mode 保留）；
applied_value 经 tau 门控（tau ∈ (0,1] > 0）必然取回退值（安全侧默认）。
"""

from jev.kernel.decisions.fallback import fallback_point
from jev.kernel.decisions.framework import Decision, DecisionPoint, Mode, Stage


def _point(**overrides) -> DecisionPoint:
    """合法 choice 决策点基准（mode=SHADOW 以验证 mode 保留语义）。"""
    fields: dict = {
        "id": "needs_retry",
        "stage": Stage.POST,
        "question": "是否需要重新生成？",
        "tau": 0.6,
        "mode": Mode.SHADOW,
        "fallback_action": "no_retry",
        "question_type": "choice",
        "options": ("retry", "no_retry"),
    }
    fields.update(overrides)
    return DecisionPoint(**fields)


def test_fallback_point_returns_safe_side_decision():
    point = _point()
    decision = fallback_point(point, "小脑后端熔断开启")

    assert isinstance(decision, Decision)
    assert decision.point_id == point.id
    assert decision.stage is Stage.POST
    assert decision.mode is Mode.SHADOW
    assert decision.value == "no_retry"
    assert decision.confidence == 0.0
    assert decision.from_fallback is True
    assert decision.reason == "小脑后端熔断开启"


def test_fallback_point_preserves_point_mode_across_all_states():
    """mode 保留对三态均成立：ACTIVE/OFF 回退决策沿用原点三态。"""
    for mode in (Mode.ACTIVE, Mode.OFF):
        point = _point(mode=mode)
        assert fallback_point(point, "任意原因").mode is mode


def test_fallback_decision_applied_value_is_fallback_action():
    """confidence=0.0 低于任何合法 tau，门控必然走回退值。"""
    point = _point()
    decision = fallback_point(point, "任何原因")
    assert decision.applied_value(point) == point.fallback_action
