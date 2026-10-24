"""蒸馏校准（design.md 流程图 D 第 4–8 步，FR12，T019）。

冻结权重重校准（CPU 毫秒级，离线复算样本携带的概率分布）：

- **温度缩放**：按 question_type 拟合 T——softmax(log(p)/T) 与 softmax
  (logit/T) 等价（平移不变）；T 在 train 上最小化 NLL（粗 log 网格 +
  三分细化，确定性）。
- **阈值扫描**：逐决策点在 train 上扫 tau 网格（0–1 步长 0.01）——
  门控语义 applied = 采信小脑（conf≥tau）否则 fallback_action；取精度
  最大者的最小 tau（升级路径数据 fallback==gold，精度随 tau 单调不降，
  最小 tau 保住采信覆盖率）。
- **留出集评估（NLL/ECE）**：test 上对比 新校准 vs 基线（T=1 + 注册表
  原 tau）；任一指标劣化 → 拒绝发布（保留旧校准文件，保守侧）。
"""

import dataclasses
import json
import logging
import math
import time
from pathlib import Path

from jev.distill.dataset import DistillDataset, TrajectorySample
from jev.errors import JevError
from jev.kernel.cerebellum.base import choice_confidence, score_confidence
from jev.kernel.decisions.framework import DecisionPoint, DecisionRegistry

logger = logging.getLogger(__name__)

_EPS = 1e-12
_TAU_GRID = [round(i * 0.01, 2) for i in range(101)]
_ACCEPT_EPS = 1e-9


@dataclasses.dataclass(frozen=True)
class SplitMetrics:
    """一个样本集上的校准指标。"""

    nll: float
    ece: float
    accuracy: float
    coverage: float


@dataclasses.dataclass(frozen=True)
class CalibrationReport:
    """校准报告：新参数 + train/test 指标 + 基线对比 + 发布判定。"""

    temperature: dict[str, float]
    tau: dict[str, float]
    train: SplitMetrics
    test: SplitMetrics
    baseline_test: SplitMetrics
    accepted: bool
    counts: dict[str, int]

    def to_dict(self) -> dict:
        return {
            "kind": "jev-distill-calibration",
            "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "temperature": self.temperature,
            "tau": self.tau,
            "metrics": {
                "train": dataclasses.asdict(self.train),
                "test": dataclasses.asdict(self.test),
                "baseline_test": dataclasses.asdict(self.baseline_test),
            },
            "accepted": self.accepted,
            "counts": self.counts,
        }


def calibrate(dataset: DistillDataset, registry: DecisionRegistry) -> CalibrationReport:
    """冻结权重重校准：train 拟合温度/阈值，test 验收（劣化拒绝）。"""
    if not dataset.test:
        raise JevError("留出集为空，无法评估校准（继续积累样本或下调阈值）")
    if not dataset.train:
        raise JevError("训练集为空，无法拟合校准参数")

    # 1) 温度缩放：按 question_type 在 train 上拟合
    temperature: dict[str, float] = {}
    by_type: dict[str, list[TrajectorySample]] = {}
    for sample in dataset.train:
        if sample.question_type in ("choice", "score"):
            by_type.setdefault(sample.question_type, []).append(sample)
    for question_type, samples in sorted(by_type.items()):
        temperature[question_type] = _fit_temperature(samples, registry)

    # 2) 阈值扫描：逐决策点在 train 上取精度最优最小 tau
    known_ids = set(registry.ids)  # 运行态注册点可能不在 distill 时注册表
    tau: dict[str, float] = {}
    by_point: dict[str, list[TrajectorySample]] = {}
    for sample in dataset.train:
        if sample.point_id in known_ids:
            by_point.setdefault(sample.point_id, []).append(sample)
    for point_id, samples in sorted(by_point.items()):
        point = registry.get(point_id)
        tau[point_id] = _scan_tau(samples, point, temperature)

    train_metrics = _evaluate(dataset.train, registry, temperature, tau)
    test_metrics = _evaluate(dataset.test, registry, temperature, tau)
    baseline = {q: 1.0 for q in temperature}
    baseline_tau = {p: registry.get(p).tau for p in tau}
    baseline_metrics = _evaluate(dataset.test, registry, baseline, baseline_tau)

    accepted = (
        test_metrics.nll <= baseline_metrics.nll + _ACCEPT_EPS
        and test_metrics.ece <= baseline_metrics.ece + _ACCEPT_EPS
    )
    if not accepted:
        logger.warning(
            "校准指标劣化（NLL %.4f→%.4f / ECE %.4f→%.4f），拒绝发布保留旧校准",
            baseline_metrics.nll, test_metrics.nll,
            baseline_metrics.ece, test_metrics.ece,
        )
    return CalibrationReport(
        temperature=temperature,
        tau=tau,
        train=train_metrics,
        test=test_metrics,
        baseline_test=baseline_metrics,
        accepted=accepted,
        counts={
            "train": len(dataset.train),
            "dev": len(dataset.dev),
            "test": len(dataset.test),
            "points": len(tau),
        },
    )


def save_calibration(report: CalibrationReport, out_path: Path | str) -> Path:
    """校准文件版本化落盘（calibration.json）。"""
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


# ----------------------------------------------------------------------
# 内部：温度/阈值拟合与指标
# ----------------------------------------------------------------------


def _scaled(probs: dict[str, float], temperature: float) -> dict[str, float]:
    """softmax(log(p)/T)——与 logits/T 等价（softmax 平移不变）。"""
    logits = {k: math.log(max(v, _EPS)) / temperature for k, v in probs.items()}
    top = max(logits.values())
    exps = {k: math.exp(v - top) for k, v in logits.items()}
    total = sum(exps.values())
    return {k: e / total for k, e in exps.items()}


def _prediction(
    sample: TrajectorySample, point: DecisionPoint, temperature: float
) -> tuple[str | None, float]:
    """温度重整后的预测标签与置信度（choice/score 公式对齐小脑实现）。"""
    probs = _scaled(sample.probabilities, temperature)
    if not probs:
        return None, 0.0
    if sample.question_type == "choice":
        return max(probs, key=probs.__getitem__), choice_confidence(probs)
    if sample.question_type == "score":
        levels = list(point.options)
        key = max(probs, key=probs.__getitem__)
        if not key.isdigit() or int(key) >= len(levels):
            return None, 0.0
        return levels[int(key)], score_confidence(probs)
    return None, 0.0


def _nll(sample: TrajectorySample, point: DecisionPoint, temperature: float) -> float | None:
    """gold 选项的负对数似然（gold 不在选项域 → None 不计入）。"""
    probs = _scaled(sample.probabilities, temperature)
    if sample.question_type == "choice":
        key = sample.gold if sample.gold in probs else None
    elif sample.question_type == "score":
        levels = list(point.options)
        key = str(levels.index(sample.gold)) if sample.gold in levels else None
    else:
        key = None
    if key is None:
        return None
    return -math.log(max(probs[key], _EPS))


def _fit_temperature(
    samples: list[TrajectorySample], registry: DecisionRegistry
) -> float:
    """最小化 NLL 的温度（log 粗网格 + 三分细化，确定性）。"""
    known_ids = set(registry.ids)
    usable = [s for s in samples if s.point_id in known_ids]
    if not usable:
        return 1.0

    def objective(t: float) -> float:
        total, count = 0.0, 0
        for sample in usable:
            point = registry.get(sample.point_id)
            value = _nll(sample, point, t)
            if value is not None:
                total += value
                count += 1
        return total / count if count else math.inf

    grid = [math.exp(math.log(0.05) + i * (math.log(20.0) - math.log(0.05)) / 79)
            for i in range(80)]
    best = min(grid, key=objective)
    low, high = best * 0.75, best * 1.33
    for _ in range(60):  # 三分法：单谷目标
        if high - low < 1e-4:
            break
        m1 = low + (high - low) / 3
        m2 = high - (high - low) / 3
        if objective(m1) <= objective(m2):
            high = m2
        else:
            low = m1
    fitted = (low + high) / 2
    return max(0.05, min(20.0, fitted))


def _scan_tau(
    samples: list[TrajectorySample], point: DecisionPoint, temperature: dict[str, float]
) -> float:
    """精度最优的最小 tau（升级路径数据 fallback==gold，精度随 tau 单调不降）。"""
    t = temperature.get(_qtype(point, samples), 1.0)
    preds = [(_prediction(s, point, t), s) for s in samples]

    def accuracy(tau: float) -> float:
        correct = 0
        for (label, conf), sample in preds:
            applied = label if (label is not None and conf >= tau) else point.fallback_action
            if applied == sample.gold:
                correct += 1
        return correct / len(preds) if preds else 0.0

    best_acc = max(accuracy(tau) for tau in _TAU_GRID)
    return min(tau for tau in _TAU_GRID if accuracy(tau) == best_acc)


def _qtype(point: DecisionPoint, samples: list[TrajectorySample]) -> str:
    for sample in samples:
        return sample.question_type
    return point.question_type


def _evaluate(
    samples: list[TrajectorySample],
    registry: DecisionRegistry,
    temperature: dict[str, float],
    tau: dict[str, float],
) -> SplitMetrics:
    """门控语义下全量指标：NLL/ECE/精度/采信覆盖率。"""
    known_ids = set(registry.ids)
    nlls: list[float] = []
    confidences: list[float] = []
    corrects: list[bool] = []
    covered = 0
    for sample in samples:
        if sample.point_id not in known_ids:
            continue
        point = registry.get(sample.point_id)
        t = temperature.get(sample.question_type, 1.0)
        gate = tau.get(sample.point_id, point.tau)
        label, conf = _prediction(sample, point, t)
        applied = label if (label is not None and conf >= gate) else point.fallback_action
        corrects.append(applied == sample.gold)
        confidences.append(conf)
        if label is not None and conf >= gate:
            covered += 1
        value = _nll(sample, point, t)
        if value is not None:
            nlls.append(value)
    total = len(corrects)
    return SplitMetrics(
        nll=sum(nlls) / len(nlls) if nlls else 0.0,
        ece=_ece(confidences, corrects),
        accuracy=sum(corrects) / total if total else 0.0,
        coverage=covered / total if total else 0.0,
    )


def _ece(confidences: list[float], corrects: list[bool], bins: int = 10) -> float:
    """标准分桶 ECE：Σ (n_b/N)·|acc_b − conf_b|。"""
    total = len(confidences)
    if total == 0:
        return 0.0
    buckets: list[list[float]] = [[] for _ in range(bins)]
    hits: list[list[bool]] = [[] for _ in range(bins)]
    for conf, correct in zip(confidences, corrects, strict=True):
        index = min(bins - 1, max(0, int(conf * bins)))
        buckets[index].append(conf)
        hits[index].append(correct)
    ece = 0.0
    for confs, corr in zip(buckets, hits, strict=True):
        if not confs:
            continue
        weight = len(confs) / total
        ece += weight * abs(
            sum(corr) / len(corr) - sum(confs) / len(confs)
        )
    return ece


__all__ = ["CalibrationReport", "SplitMetrics", "calibrate", "save_calibration"]
