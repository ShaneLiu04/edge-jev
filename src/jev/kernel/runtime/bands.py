"""三带策略（AR003 FR1-2，design §4.2.2(3)）。

confident: U ≤ tau_low；borderline: tau_low < U < tau_high；uncertain: U ≥ tau_high。
NaN / 非法 U 一律按 uncertain（安全侧）。
"""

from __future__ import annotations

import dataclasses
import math

CONFIDENT = "confident"
BORDERLINE = "borderline"
UNCERTAIN = "uncertain"

_MODES = ("observe", "gate")


@dataclasses.dataclass(frozen=True)
class BandPolicy:
    tau_low: float = 0.35
    tau_high: float = 0.65
    verifier_enabled: bool = False
    self_verify_mode: str = "observe"
    gate_threshold: float = 0.60

    def __post_init__(self) -> None:
        if not 0.0 <= self.tau_low < self.tau_high <= 1.0:
            raise ValueError(
                f"三带阈值须满足 0 ≤ tau_low < tau_high ≤ 1: "
                f"{self.tau_low} / {self.tau_high}"
            )
        if not 0.0 <= self.gate_threshold <= 1.0:
            raise ValueError(f"gate_threshold 须在 [0,1]: {self.gate_threshold}")
        if self.self_verify_mode not in _MODES:
            raise ValueError(f"self_verify_mode 非法: {self.self_verify_mode!r}")


def classify_band(u: float, policy: BandPolicy | None = None) -> str:
    """按 U 分类；NaN/非数值 → uncertain。"""
    policy = policy if policy is not None else BandPolicy()
    if not isinstance(u, (int, float)) or math.isnan(float(u)):
        return UNCERTAIN
    value = min(1.0, max(0.0, float(u)))
    if value <= policy.tau_low:
        return CONFIDENT
    if value >= policy.tau_high:
        return UNCERTAIN
    return BORDERLINE


def apply_self_verify_gate(
    band: str, self_agree: float, policy: BandPolicy
) -> str:
    """gate 模式：低自证把 band 至少提升为 borderline（<0.4 → uncertain）。"""
    if policy.self_verify_mode != "gate":
        return band
    if self_agree >= policy.gate_threshold:
        return band
    if self_agree < 0.40:
        return UNCERTAIN
    if band == CONFIDENT:
        return BORDERLINE
    return band


__all__ = [
    "BORDERLINE",
    "CONFIDENT",
    "UNCERTAIN",
    "BandPolicy",
    "apply_self_verify_gate",
    "classify_band",
]
