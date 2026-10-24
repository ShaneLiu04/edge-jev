"""蒸馏数据集构造（design.md 流程图 D 第 1–3 步，FR12，T018）。

从台账抽取大脑升级路径轨迹（route=full 且 POST 复核通过——大脑确实
接手且交付合格），逐决策点构造 (state, question, gold) 三元组：

- **gold 标注（outcome 推导）**：升级路径请求是"确实需要大脑"的正样
  本——PRE 点 gold 取"需要大脑"侧选项（intent→complex 等）；POST 复核
  通过意味着交付答案完整/切题——POST 点 gold 取"通过"侧选项
  （needs_retry→no_retry 等）。无法从结果推导 gold 的点（verbosity 等
  主观项）不入集。
- **小脑当时预测**（value/confidence/probabilities/from_fallback）随
  样本保留——T019 温度缩放/阈值扫描可离线复算，无需重跑 ONNX 前向。
- **gold_answer**（大脑最终采纳答案文本）随样本保留——T020 kev LoRA
  行为蒸馏用。

去重按 (state, point_id)；分层切分按 point_id（train/dev/test=70/15/15，
层内确定性排序）。
"""

import dataclasses
import json
import logging
import math
from pathlib import Path

from jev.errors import JevError
from jev.kernel.ledger.query import LedgerQuery
from jev.kernel.ledger.writer import LedgerEntry

logger = logging.getLogger(__name__)

# 升级路径正样本的 outcome 推导 gold（点 id → 选项文本；不入表 = 不入集）
GOLD_LABELS: dict[str, str] = {
    # PRE：请求确实需要大脑
    "intent": "complex",
    "needs_generation": "generation",
    "needs_reasoning": "reasoning",
    "complexity": "high",
    "safety": "safe",
    "needs_tool": "tool",
    # POST：复核通过的交付答案
    "needs_retry": "no_retry",
    "answer_complete": "complete",
    "matches_request": "matches",
    "correct_format": "correct",
    "too_verbose": "not_verbose",
}

TRAIN_RATIO = 0.7
DEV_RATIO = 0.15


@dataclasses.dataclass(frozen=True)
class TrajectorySample:
    """单条蒸馏样本：一个请求的一个决策点。"""

    request_id: str
    point_id: str
    state: str
    question: str
    question_type: str
    gold: str
    gold_answer: str
    value: str | float
    confidence: float
    probabilities: dict[str, float]
    from_fallback: bool


@dataclasses.dataclass(frozen=True)
class DistillDataset:
    """蒸馏数据集：样本全集 + 分层切分（train/dev/test）。"""

    samples: tuple[TrajectorySample, ...]
    train: tuple[TrajectorySample, ...]
    dev: tuple[TrajectorySample, ...]
    test: tuple[TrajectorySample, ...]

    @property
    def strata(self) -> dict[str, int]:
        """逐决策点样本计数（切分分层键）。"""
        counts: dict[str, int] = {}
        for sample in self.samples:
            counts[sample.point_id] = counts.get(sample.point_id, 0) + 1
        return counts


def build_dataset(query: LedgerQuery, *, min_samples: int = 500) -> DistillDataset:
    """从台账抽取升级路径轨迹并构造数据集（不足 min_samples 拒绝）。"""
    routes: dict[str, LedgerEntry] = {}
    answers: dict[str, LedgerEntry] = {}
    decisions: dict[str, list[LedgerEntry]] = {}
    for entry in query.iter_entries():
        if entry.kind == "route":
            routes[entry.request_id] = entry
        elif entry.kind == "answer":
            answers[entry.request_id] = entry
        elif entry.kind == "decision":
            decisions.setdefault(entry.request_id, []).append(entry)

    samples: list[TrajectorySample] = []
    seen: set[tuple[str, str]] = set()
    for request_id in sorted(routes):
        route = routes[request_id]
        answer = answers.get(request_id)
        # 升级路径：大脑接手（full）且 POST 复核通过（accept）
        if route.route != "full" or answer is None or answer.post_verdict != "accept":
            continue
        if route.state is None or answer.content is None:
            continue
        for decision in decisions.get(request_id, []):
            gold = GOLD_LABELS.get(decision.point_id)
            if gold is None or decision.question is None:
                continue
            key = (route.state, decision.point_id)
            if key in seen:  # 同 state 同点去重（保留首见）
                continue
            seen.add(key)
            samples.append(
                TrajectorySample(
                    request_id=request_id,
                    point_id=decision.point_id,
                    state=route.state,
                    question=decision.question,
                    question_type=decision.question_type or "choice",
                    gold=gold,
                    gold_answer=answer.content,
                    value=decision.value if decision.value is not None else "",
                    confidence=float(decision.confidence or 0.0),
                    probabilities={
                        str(k): float(v)
                        for k, v in (decision.probabilities or {}).items()
                    },
                    from_fallback=bool(decision.from_fallback),
                )
            )

    if len(samples) < min_samples:
        raise JevError(
            f"蒸馏样本不足：抽取 {len(samples)} 条 < 最小阈值 {min_samples}"
            "（继续积累台账轨迹或显式下调 --min-samples）"
        )

    train, dev, test = _stratified_split(samples)
    dataset = DistillDataset(
        samples=tuple(samples),
        train=tuple(train),
        dev=tuple(dev),
        test=tuple(test),
    )
    logger.info(
        "蒸馏数据集构造完成: %d 样本 / %d 决策点，切分 %d/%d/%d",
        len(samples), len(dataset.strata), len(train), len(dev), len(test),
    )
    return dataset


def _stratified_split(
    samples: list[TrajectorySample],
) -> tuple[list[TrajectorySample], list[TrajectorySample], list[TrajectorySample]]:
    """按 point_id 分层 70/15/15（层内确定性排序，小层保底 train）。"""
    strata: dict[str, list[TrajectorySample]] = {}
    for sample in samples:
        strata.setdefault(sample.point_id, []).append(sample)
    train: list[TrajectorySample] = []
    dev: list[TrajectorySample] = []
    test: list[TrajectorySample] = []
    for point_id in sorted(strata):
        group = sorted(strata[point_id], key=lambda s: (s.request_id, s.question))
        n_train = math.floor(len(group) * TRAIN_RATIO)
        n_dev = math.floor(len(group) * DEV_RATIO)
        train.extend(group[:n_train])
        dev.extend(group[n_train : n_train + n_dev])
        test.extend(group[n_train + n_dev :])
    return train, dev, test


def export_dataset(dataset: DistillDataset, out_dir: Path | str) -> Path:
    """数据集版本化导出：train/dev/test JSONL + manifest.json。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    splits = {"train": dataset.train, "dev": dataset.dev, "test": dataset.test}
    for name, samples in splits.items():
        path = out / f"{name}.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for sample in samples:
                fh.write(
                    json.dumps(dataclasses.asdict(sample), ensure_ascii=False) + "\n"
                )
    manifest = {
        "kind": "jev-distill-dataset",
        "counts": {
            "total": len(dataset.samples),
            "train": len(dataset.train),
            "dev": len(dataset.dev),
            "test": len(dataset.test),
        },
        "strata": dataset.strata,
        "split_ratios": {"train": TRAIN_RATIO, "dev": DEV_RATIO},
        "gold_labels": GOLD_LABELS,
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return out


__all__ = ["GOLD_LABELS", "TrajectorySample", "DistillDataset", "build_dataset", "export_dataset"]
