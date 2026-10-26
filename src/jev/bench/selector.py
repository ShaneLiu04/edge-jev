"""方案选择与自动回退（AR005 FR5-4，design §4.2.2）。

输入真实矩阵的逐题结果，输出 adopt/rollback：
- 效用 `quality - λ * normalized_cost`；
- 按题配对 bootstrap 95% CI；
- 留一交叉验证评估 band→arm 策略，防止小样本过拟合；
- 无显著正下界或质量下降 → rollback 到基线 arm。
"""

from __future__ import annotations

import dataclasses
import random
from collections import defaultdict
from collections.abc import Sequence

MIN_GAIN = 0.0


@dataclasses.dataclass(frozen=True)
class ArmOutcome:
    arm: str
    question_id: str
    quality: float
    tokens: int
    latency_ms: float
    band: str | None = None


@dataclasses.dataclass(frozen=True)
class ArmMetrics:
    quality: float
    mean_tokens: float
    mean_latency_ms: float
    count: int


@dataclasses.dataclass(frozen=True)
class SelectionResult:
    decision: str
    chosen_arm: str
    baseline_arm: str
    utility: dict[str, float]
    delta_ci: tuple[float, float] | None
    policy: dict[str, str]
    reason: str


def _arm_metrics(outcomes: Sequence[ArmOutcome]) -> dict[str, ArmMetrics]:
    grouped: dict[str, list[ArmOutcome]] = defaultdict(list)
    for outcome in outcomes:
        grouped[outcome.arm].append(outcome)
    metrics: dict[str, ArmMetrics] = {}
    for arm, rows in grouped.items():
        count = len(rows)
        metrics[arm] = ArmMetrics(
            quality=sum(row.quality for row in rows) / count,
            mean_tokens=sum(row.tokens for row in rows) / count,
            mean_latency_ms=sum(row.latency_ms for row in rows) / count,
            count=count,
        )
    return metrics


def _utilities(metrics: dict[str, ArmMetrics], lambda_cost: float) -> dict[str, float]:
    max_tokens = max((item.mean_tokens for item in metrics.values()), default=0.0)
    if max_tokens <= 0:
        max_tokens = 1.0
    return {
        arm: item.quality - lambda_cost * (item.mean_tokens / max_tokens)
        for arm, item in metrics.items()
    }


def _paired_delta(
    left: Sequence[float], right: Sequence[float], *, seed: int, rounds: int = 1000
) -> tuple[float, float]:
    rng = random.Random(seed)
    n = len(left)
    deltas = []
    for _ in range(rounds):
        total = 0.0
        for _ in range(n):
            index = rng.randrange(n)
            total += left[index] - right[index]
        deltas.append(total / n)
    deltas.sort()
    low = deltas[int(0.025 * (rounds - 1))]
    high = deltas[int(0.975 * (rounds - 1))]
    return low, high


def _per_question_utility(
    outcomes: Sequence[ArmOutcome], arm: str, lambda_cost: float, max_tokens: float
) -> dict[str, float]:
    return {
        row.question_id: row.quality - lambda_cost * (row.tokens / max_tokens)
        for row in outcomes
        if row.arm == arm
    }


def _band_policy(
    outcomes: Sequence[ArmOutcome],
    arms: Sequence[str],
    lambda_cost: float,
    max_tokens: float,
) -> dict[str, str]:
    scores: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in outcomes:
        band = row.band or "unknown"
        scores[(band, row.arm)].append(
            row.quality - lambda_cost * (row.tokens / max_tokens)
        )
    policy: dict[str, str] = {}
    for band in sorted({band for band, _ in scores}):
        policy[band] = max(
            arms,
            key=lambda arm: (
                sum(scores[(band, arm)]) / len(scores[(band, arm)])
                if scores[(band, arm)]
                else float("-inf"),
                arm,
            ),
        )
    return policy


def _quality_by_question(
    outcomes: Sequence[ArmOutcome],
) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = defaultdict(dict)
    for row in outcomes:
        result[row.arm][row.question_id] = row.quality
    return result


def _loo_policy(
    outcomes: Sequence[ArmOutcome],
    arms: Sequence[str],
    lambda_cost: float,
    max_tokens: float,
) -> tuple[dict[str, float], dict[str, float], dict[str, str]]:
    """留一评估 band→arm 策略；返回逐题效用/质量与全量策略（仅用于导出）。"""
    questions = sorted({row.question_id for row in outcomes})
    by_question = {(row.question_id, row.arm): row for row in outcomes}
    utility: dict[str, float] = {}
    quality: dict[str, float] = {}
    for held_out in questions:
        train = [row for row in outcomes if row.question_id != held_out]
        policy = _band_policy(train, arms, lambda_cost, max_tokens)
        bands = sorted(
            {
                row.band or "unknown"
                for row in outcomes
                if row.question_id == held_out
            }
        )
        arm = policy.get(bands[0]) if bands else None
        row = by_question.get((held_out, arm)) if arm else None
        if row is not None:
            utility[held_out] = row.quality - lambda_cost * (row.tokens / max_tokens)
            quality[held_out] = row.quality
    return (
        utility,
        quality,
        _band_policy(outcomes, arms, lambda_cost, max_tokens),
    )


def select(
    outcomes: Sequence[ArmOutcome],
    *,
    lambda_cost: float = 0.15,
    alpha: float = 0.05,
    baseline_arm: str = "B2",
    seed: int = 20261026,
    min_samples: int = 20,
) -> SelectionResult:
    if not outcomes:
        return SelectionResult(
            "rollback", baseline_arm, baseline_arm, {}, None, {}, "无样本"
        )
    if lambda_cost < 0:
        raise ValueError(f"lambda_cost 不得为负: {lambda_cost}")
    metrics = _arm_metrics(outcomes)
    questions = sorted({row.question_id for row in outcomes})
    if len(questions) < min_samples:
        return SelectionResult(
            "rollback",
            baseline_arm,
            baseline_arm,
            _utilities(metrics, lambda_cost),
            None,
            {},
            f"样本不足（{len(questions)} < {min_samples}）",
        )

    utility = _utilities(metrics, lambda_cost)
    if baseline_arm not in metrics:
        baseline_arm = max(utility, key=utility.get)
    max_tokens = max((item.mean_tokens for item in metrics.values()), default=1.0) or 1.0
    base_per_q = _per_question_utility(outcomes, baseline_arm, lambda_cost, max_tokens)
    quality_map = _quality_by_question(outcomes)

    candidates: dict[str, tuple[dict[str, float], dict[str, str]]] = {}
    for arm in sorted(metrics):
        if arm == baseline_arm:
            continue
        candidates[arm] = (
            _per_question_utility(outcomes, arm, lambda_cost, max_tokens),
            {},
        )
    loo_utility, loo_quality, full_policy = _loo_policy(
        outcomes, sorted(metrics), lambda_cost, max_tokens
    )
    candidates["POLICY"] = (loo_utility, full_policy)

    best_arm = baseline_arm
    best_policy: dict[str, str] = {}
    best_ci: tuple[float, float] | None = None
    best_reason = "候选无显著正下界，回退基线"
    for arm in sorted(candidates):
        per_q, policy_map = candidates[arm]
        common = sorted(set(base_per_q) & set(per_q))
        if len(common) < min_samples:
            continue
        left = [per_q[qid] for qid in common]
        right = [base_per_q[qid] for qid in common]
        low, high = _paired_delta(left, right, seed=seed)
        if arm == "POLICY":
            quality = sum(loo_quality.get(qid, 0.0) for qid in common) / len(common)
        else:
            quality = sum(quality_map[arm].get(qid, 0.0) for qid in common) / len(common)
        base_quality = metrics[baseline_arm].quality
        if quality >= base_quality - 0.01 and low > MIN_GAIN:
            if best_ci is None or low > best_ci[0]:
                best_arm = arm
                best_policy = policy_map
                best_ci = (low, high)
                best_reason = f"{arm} 效用下界 {low:.4f} > 0"
    if best_arm == baseline_arm or best_ci is None:
        return SelectionResult(
            "rollback",
            baseline_arm,
            baseline_arm,
            utility,
            None,
            {},
            best_reason,
        )
    return SelectionResult(
        "adopt",
        best_arm,
        baseline_arm,
        utility,
        best_ci,
        best_policy,
        best_reason,
    )


__all__ = ["ArmMetrics", "ArmOutcome", "SelectionResult", "select"]
