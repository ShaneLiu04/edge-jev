"""不确定性量化（AR003 FR1-1，design §4.2.2(1)）。

U 为**归一化凸组合**，权重和恒为 1，值域 [0,1]：

    U = w_c·(1-conf) + w_m·(1-margin) + w_p·disagreement + w_x·(1-self_agree)

- 缺省维度（无 margin / 无多探针）按剩余权重重新归一；
- `self_agree` 除外：缺失固定为 1.0（不升高 U）且保留权重（与 SRS 口径一致）；
- 无 ACTIVE 决策 → U=1.0（安全侧，进 uncertain）。
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping, Sequence

from jev.kernel.decisions.framework import Decision, Mode
from jev.kernel.decisions.probes import FusedPoint
from jev.kernel.runtime.selfcheck import self_agreement

_EPS = 1e-12


@dataclasses.dataclass(frozen=True)
class UncertaintyWeights:
    confidence: float = 0.40
    margin: float = 0.25
    probe: float = 0.20
    self_agree: float = 0.15

    def __post_init__(self) -> None:
        values = (self.confidence, self.margin, self.probe, self.self_agree)
        if any(value < 0 for value in values):
            raise ValueError(f"不确定性权重不得为负: {values}")
        if sum(values) <= 0:
            raise ValueError("不确定性权重之和须 > 0")

    @property
    def total(self) -> float:
        return self.confidence + self.margin + self.probe + self.self_agree


@dataclasses.dataclass(frozen=True)
class Uncertainty:
    """请求级不确定性分解（供路由/台账）。"""

    u: float
    total_margin: float | None
    disagreement: float | None
    self_agree: float
    probe_agreement: float | None


def js_divergence(p: Mapping[str, float], q: Mapping[str, float]) -> float:
    """Jensen-Shannon 散度（log2），值域 [0,1]；缺失标签视为 0。"""
    labels = set(p) | set(q)
    if not labels:
        return 0.0
    midpoint = {label: (_prob(p, label) + _prob(q, label)) / 2.0 for label in labels}
    divergence = (
        _kl(p, midpoint) + _kl(q, midpoint)
    ) / 2.0
    return min(1.0, max(0.0, divergence))


def _prob(distribution: Mapping[str, float], label: str) -> float:
    return max(0.0, float(distribution.get(label, 0.0)))


def _kl(distribution: Mapping[str, float], reference: Mapping[str, float]) -> float:
    total = 0.0
    for label, probability in reference.items():
        probability = max(probability, _EPS)
        value = _prob(distribution, label)
        if value > 0:
            total += value * math.log2(value / probability)
    return total


def _active(decisions: Sequence[Decision]) -> list[Decision]:
    return [decision for decision in decisions if decision.mode is Mode.ACTIVE]


def _confidence_term(decisions: Sequence[Decision]) -> float | None:
    values = [min(1.0, max(0.0, decision.confidence)) for decision in decisions]
    if not values:
        return None
    # 几何平均：任一点低置信（含 0）都会显著抬高不确定性
    return math.exp(sum(math.log(max(value, _EPS)) for value in values) / len(values))


def _margin_term(decisions: Sequence[Decision]) -> float | None:
    margins = [decision.margin for decision in decisions if decision.margin is not None]
    if not margins:
        return None
    return sum(min(1.0, max(0.0, margin)) for margin in margins) / len(margins)


def _disagreement_term(fused: Mapping[str, FusedPoint]) -> float | None:
    divergences: list[float] = []
    for point in fused.values():
        distributions = list(point.probes.values())
        if len(distributions) < 2:
            continue
        pair_values: list[float] = []
        for index, first in enumerate(distributions):
            for second in distributions[index + 1:]:
                pair_values.append(js_divergence(first, second))
        if pair_values:
            divergences.append(sum(pair_values) / len(pair_values))
    if not divergences:
        return None
    return sum(divergences) / len(divergences)


def estimate_uncertainty(
    decisions: Sequence[Decision],
    fused: Mapping[str, FusedPoint],
    *,
    weights: UncertaintyWeights | None = None,
) -> Uncertainty:
    """合成请求级不确定性；空决策批 → U=1.0（安全侧）。"""
    weights = weights if weights is not None else UncertaintyWeights()
    active = _active(decisions)
    if not active:
        return Uncertainty(
            u=1.0,
            total_margin=None,
            disagreement=None,
            self_agree=1.0,
            probe_agreement=None,
        )

    confidence = _confidence_term(active)
    margin = _margin_term(active)
    disagreement = _disagreement_term(fused)
    evidence = self_agreement(fused) if fused else None
    self_agree = evidence.consistency if evidence is not None else 1.0
    probe_agreement = evidence.consistency if evidence is not None else None

    components: list[tuple[float, float]] = []
    if confidence is not None:
        components.append((weights.confidence, 1.0 - confidence))
    if margin is not None:
        components.append((weights.margin, 1.0 - margin))
    if disagreement is not None:
        components.append((weights.probe, disagreement))
    # self_agree 始终保留权重（缺失=1 → 贡献 0）
    components.append((weights.self_agree, 1.0 - self_agree))

    weight_sum = sum(weight for weight, _ in components)
    if weight_sum <= 0:
        return Uncertainty(1.0, margin, disagreement, self_agree, probe_agreement)
    value = sum(weight * term for weight, term in components) / weight_sum
    return Uncertainty(
        u=min(1.0, max(0.0, value)),
        total_margin=margin,
        disagreement=disagreement,
        self_agree=self_agree,
        probe_agreement=probe_agreement,
    )


__all__ = [
    "Uncertainty",
    "UncertaintyWeights",
    "estimate_uncertainty",
    "js_divergence",
]
