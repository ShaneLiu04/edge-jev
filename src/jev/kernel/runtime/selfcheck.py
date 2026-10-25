"""自证信号（AR003 FR1-2，design §4.2.2(2)）。

复用 AR002 置换/多探针产物（`FusedPoint.probes`），不产生额外前向：
- consistency：各探针 top-1 与融合 top-1 的一致比例（按点平均）；
- cross_point：探针之间完全一致的点比例（"同义点组"的代理口径）；
- 无探针/单探针 → 视为一致（1.0），避免虚高不确定性。
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping

from jev.kernel.decisions.probes import FusedPoint


@dataclasses.dataclass(frozen=True)
class SelfEvidence:
    consistency: float
    cross_point: float
    mode: str = "observe"  # observe | gate


def _top_label(distribution: Mapping[str, float]) -> str | None:
    if not distribution:
        return None
    return max(distribution, key=distribution.__getitem__)


def self_agreement(fused: Mapping[str, FusedPoint]) -> SelfEvidence:
    """计算自证一致率；空 fused → 1.0（不惩罚未知）。"""
    consistencies: list[float] = []
    unanimous: list[bool] = []
    for point in fused.values():
        fused_top = point.value
        probe_tops = [_top_label(distribution) for distribution in point.probes.values()]
        probe_tops = [label for label in probe_tops if label is not None]
        if not probe_tops:
            continue
        agree = sum(1 for label in probe_tops if label == fused_top) / len(probe_tops)
        consistencies.append(agree)
        unanimous.append(all(label == probe_tops[0] for label in probe_tops))
    if not consistencies:
        return SelfEvidence(consistency=1.0, cross_point=1.0)
    return SelfEvidence(
        consistency=sum(consistencies) / len(consistencies),
        cross_point=sum(1 for flag in unanimous if flag) / len(unanimous),
    )


__all__ = ["SelfEvidence", "self_agreement"]
