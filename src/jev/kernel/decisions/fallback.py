"""安全侧回退：小脑不可用/超时/低置信时按 fallback_action 出决策（srs FR4）。"""

from jev.kernel.decisions.framework import Decision, DecisionPoint


def fallback_point(point: DecisionPoint, reason: str) -> Decision:
    """产出安全侧决策：value=fallback_action、confidence=0（必然走 tau 门控回退）。"""
    return Decision(
        point_id=point.id,
        stage=point.stage,
        mode=point.mode,
        value=point.fallback_action,
        confidence=0.0,
        from_fallback=True,
        reason=reason,
    )
