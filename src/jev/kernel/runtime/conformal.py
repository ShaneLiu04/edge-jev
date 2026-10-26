"""Conformal 风险控制（AR005 FR5-1/FR5-2，design §4.2.2）。

用真实标注样本给出有限样本风险上界，替代固定阈值：
- 降级方向 `le`：风险分 s <= t 视为可降级，风险 = 降级样本的质量损失；
- 准入方向 `ge`：相似度 s >= t 视为可命中，风险 = 负例被误准入的比例。

阈值选择采用 Conformal Risk Control 的经验界
`(sum_loss + 1) / (n + 1) <= alpha`（Angelopoulos et al., 2022）。
校准样本不足或无可行阈值时显式 disabled，调用方回退既有行为。
"""

from __future__ import annotations

import dataclasses
import json
import math
from collections.abc import Sequence
from pathlib import Path

from jev.errors import JevProtocolError

_MIN_SAMPLES = 20
_DIRECTIONS = ("le", "ge")


@dataclasses.dataclass(frozen=True)
class CalibrationResult:
    enabled: bool
    threshold: float | None
    coverage: float
    risk: float
    bound: float
    n: int
    alpha: float
    direction: str

    def stats(self) -> dict:
        return {
            "enabled": self.enabled,
            "threshold": self.threshold,
            "coverage": round(self.coverage, 6),
            "risk": round(self.risk, 6),
            "bound": round(self.bound, 6),
            "n": self.n,
            "alpha": self.alpha,
            "direction": self.direction,
        }


def _validate(scores: Sequence[float], losses: Sequence[float], alpha: float) -> None:
    if len(scores) != len(losses):
        raise ValueError(f"scores/losses 长度不一致: {len(scores)} != {len(losses)}")
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha 须在 (0,1): {alpha}")
    for value in scores:
        if not isinstance(value, (int, float)) or math.isnan(float(value)):
            raise ValueError(f"score 非法: {value!r}")
    for value in losses:
        if not isinstance(value, (int, float)) or math.isnan(float(value)):
            raise ValueError(f"loss 非法: {value!r}")
        if not 0.0 <= float(value) <= 1.0:
            raise ValueError(f"loss 须在 [0,1]: {value!r}")


def conformal_risk_threshold(
    scores: Sequence[float],
    losses: Sequence[float],
    *,
    alpha: float = 0.10,
    direction: str = "le",
    min_samples: int = _MIN_SAMPLES,
) -> CalibrationResult:
    """选择满足风险上界的最小/最大阈值；不可行时 enabled=False。"""
    if direction not in _DIRECTIONS:
        raise ValueError(f"direction 非法: {direction!r}")
    _validate(scores, losses, alpha)
    n = len(scores)
    disabled = CalibrationResult(
        enabled=False,
        threshold=None,
        coverage=0.0,
        risk=0.0,
        bound=1.0,
        n=n,
        alpha=alpha,
        direction=direction,
    )
    if n < min_samples:
        return disabled

    candidates = sorted({float(value) for value in scores}, reverse=direction == "ge")
    best: CalibrationResult | None = None
    for threshold in candidates:
        if direction == "le":
            accepted = [i for i, value in enumerate(scores) if float(value) <= threshold]
        else:
            accepted = [i for i, value in enumerate(scores) if float(value) >= threshold]
        if not accepted:
            continue
        total_loss = sum(float(losses[i]) for i in accepted)
        bound = (total_loss + 1.0) / (n + 1)
        coverage = len(accepted) / n
        risk = total_loss / len(accepted)
        if bound > alpha:
            continue
        candidate = CalibrationResult(
            enabled=True,
            threshold=threshold,
            coverage=coverage,
            risk=risk,
            bound=bound,
            n=n,
            alpha=alpha,
            direction=direction,
        )
        if best is None or coverage > best.coverage:
            best = candidate
    return best if best is not None else disabled


@dataclasses.dataclass(frozen=True)
class AdmissionPolicy:
    """语义缓存准入策略（ge 方向），失败安全默认 disabled。"""

    enabled: bool
    threshold: float | None
    alpha: float
    margin: float
    recall: float
    n_negative: int

    def allows(self, score: float, second: float) -> bool:
        if not self.enabled or self.threshold is None:
            return False
        return score >= self.threshold and (score - second) >= self.margin

    def stats(self) -> dict:
        return {
            "enabled": self.enabled,
            "threshold": self.threshold,
            "alpha": self.alpha,
            "margin": self.margin,
            "recall": round(self.recall, 6),
            "n_negative": self.n_negative,
            "admission": "conformal",
        }


def load_admission(path: Path | str) -> AdmissionPolicy:
    """读取矩阵导出的缓存准入校准（失败 fail-fast）。"""
    file = Path(path)
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise JevProtocolError(f"缓存准入校准文件不可用: {file}: {exc}") from exc
    if not isinstance(data, dict) or not bool(data.get("enabled")):
        raise JevProtocolError(f"缓存准入校准未启用/格式非法: {file}")
    try:
        return AdmissionPolicy(
            enabled=True,
            threshold=float(data["threshold"]),
            alpha=float(data.get("alpha", 0.1)),
            margin=float(data.get("margin", 0.02)),
            recall=float(data.get("recall", 0.0)),
            n_negative=int(data.get("n_negative", 0)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise JevProtocolError(f"缓存准入字段缺失/非法: {file}: {exc}") from exc


def calibrate_cache_admission(
    positive_scores: Sequence[float],
    negative_scores: Sequence[float],
    *,
    alpha: float = 0.10,
    margin: float = 0.02,
    min_recall: float = 0.60,
) -> AdmissionPolicy:
    """负例控制假命中，并要求正例（原句/复述）召回达标。"""
    result = conformal_risk_threshold(
        negative_scores,
        [1.0] * len(negative_scores),
        alpha=alpha,
        direction="ge",
    )
    if not result.enabled or result.threshold is None:
        return AdmissionPolicy(False, None, alpha, margin, 0.0, len(negative_scores))
    threshold = float(result.threshold)
    recall = (
        sum(1 for value in positive_scores if float(value) >= threshold)
        / len(positive_scores)
        if positive_scores
        else 0.0
    )
    if recall < min_recall:
        return AdmissionPolicy(False, None, alpha, margin, recall, len(negative_scores))
    return AdmissionPolicy(
        enabled=True,
        threshold=threshold,
        alpha=alpha,
        margin=margin,
        recall=recall,
        n_negative=len(negative_scores),
    )


class RiskController:
    """运行期风险门：只做单调比较，禁用时完全回退既有语义。"""

    def __init__(self, result: CalibrationResult) -> None:
        self._result = result

    @classmethod
    def disabled(cls) -> RiskController:
        return cls(
            CalibrationResult(False, None, 0.0, 0.0, 1.0, 0, 0.1, "le")
        )

    @classmethod
    def from_file(cls, path: Path | str, *, alpha: float = 0.10) -> RiskController:
        file = Path(path)
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise JevProtocolError(f"风险校准文件不可用: {file}: {exc}") from exc
        if not isinstance(data, dict):
            raise JevProtocolError(f"风险校准文件格式非法: {file}")
        try:
            threshold = float(data["threshold"])
            n = int(data["n"])
            risk = float(data.get("risk", 0.0))
            bound = float(data.get("bound", 1.0))
            coverage = float(data.get("coverage", 0.0))
            file_alpha = float(data.get("alpha", alpha))
            direction = str(data.get("direction", "le"))
        except (KeyError, TypeError, ValueError) as exc:
            raise JevProtocolError(f"风险校准字段缺失/非法: {file}: {exc}") from exc
        if direction != "le" or not 0.0 <= threshold <= 1.0 or n < _MIN_SAMPLES:
            raise JevProtocolError(f"风险校准不满足启用条件: {file}")
        return cls(
            CalibrationResult(
                True, threshold, coverage, risk, bound, n, file_alpha, direction
            )
        )

    @property
    def enabled(self) -> bool:
        return self._result.enabled

    @property
    def threshold(self) -> float | None:
        return self._result.threshold

    @property
    def alpha(self) -> float:
        return self._result.alpha

    def allow_downgrade(self, score: float) -> bool:
        if not self.enabled or self.threshold is None:
            return True
        if not isinstance(score, (int, float)) or math.isnan(float(score)):
            return False
        return float(score) <= self.threshold

    def stats(self) -> dict:
        return self._result.stats()


__all__ = [
    "AdmissionPolicy",
    "CalibrationResult",
    "RiskController",
    "calibrate_cache_admission",
    "conformal_risk_threshold",
    "load_admission",
]
