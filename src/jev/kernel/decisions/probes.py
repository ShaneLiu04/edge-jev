"""多探针决策引擎：探针规划、选项置换、概率融合（AR002 FR1-1/FR1-2）。

设计要点（design.md §4.2/§4.4）：
- 同一决策点的 K 个异构探针 × N 个选项置换作为**同一批问题**提交，
  复用小脑「单次编码多问」能力，不增加后端往返。
- 置换只打乱选项**顺序**，语义键（选项名）不变，因此归并天然顺序无关。
- 融合采用对数意见池（log-opinion pool）：p ∝ exp(Σ w_k·log p_k)，
  各探针先做温度缩放 p^(1/T) 归一，再按权重池化。
- 置信度取融合分布最大值；margin 取 top1-top2（Noul 用 2·|p-0.5|）。

向后兼容：点未声明探针且 permutations=1 时，问题 qid 与推理路径与 AR001
完全一致（qid = point.id）。
"""

from __future__ import annotations

import dataclasses
import math
import random
from collections.abc import Mapping, Sequence

from jev.errors import JevProtocolError
from jev.kernel.cerebellum.base import (
    Answer,
    ChoiceAnswer,
    ChoiceQuestion,
    NoulQuestion,
    Question,
    ScoreAnswer,
    ScoreQuestion,
)

EPS = 1e-9
MAX_PERMUTATIONS = 8
_QUESTION_TYPES = ("choice", "noul", "score")


@dataclasses.dataclass(frozen=True)
class ProbeSpec:
    """一种探针问法（同一决策点内的异构视角）。"""

    name: str
    question_type: str
    question: str
    options: tuple[str, ...] = ()
    weight: float = 1.0

    def validated(self, point_id: str) -> ProbeSpec:
        if not isinstance(self.name, str) or not self.name:
            raise JevProtocolError(f"{point_id}: 探针 name 必选且非空")
        if self.question_type not in _QUESTION_TYPES:
            raise JevProtocolError(
                f"{point_id}: 探针 {self.name} question_type 非法: {self.question_type!r}"
            )
        if not isinstance(self.question, str) or not self.question:
            raise JevProtocolError(f"{point_id}: 探针 {self.name} question 必选且非空")
        if not isinstance(self.weight, (int, float)) or isinstance(self.weight, bool):
            raise JevProtocolError(f"{point_id}: 探针 {self.name} weight 须为数值")
        if not math.isfinite(self.weight) or self.weight <= 0:
            raise JevProtocolError(f"{point_id}: 探针 {self.name} weight 须为正有限数")
        if self.question_type in ("choice", "score"):
            if len(self.options) < 2:
                raise JevProtocolError(
                    f"{point_id}: 探针 {self.name} 需至少 2 个 options"
                )
            if len(set(self.options)) != len(self.options):
                raise JevProtocolError(f"{point_id}: 探针 {self.name} options 重复")
        elif self.options:
            raise JevProtocolError(f"{point_id}: 探针 {self.name} noul 不携带 options")
        return self


@dataclasses.dataclass(frozen=True)
class ProbePlan:
    """单点的评估计划：qid -> 问题；qid -> 置换后的语义顺序。"""

    point_id: str
    questions: dict[str, Question]
    permuted: dict[str, tuple[str, ...]]
    probe_of: dict[str, str]  # qid -> probe name

    @property
    def qids(self) -> tuple[str, ...]:
        return tuple(self.questions)


@dataclasses.dataclass(frozen=True)
class PointCalibration:
    """Per-point 校准：温度 + 路由阈值覆盖。"""

    temperature: float = 1.0
    tau: float | None = None
    margin: float | None = None

    def validated(self, point_id: str) -> PointCalibration:
        if not math.isfinite(self.temperature) or self.temperature <= 0:
            raise JevProtocolError(f"{point_id}: 校准温度须为正有限数")
        if self.tau is not None and not 0 < self.tau <= 1:
            raise JevProtocolError(f"{point_id}: 校准 tau 须在 (0, 1]")
        if self.margin is not None and not 0 <= self.margin <= 1:
            raise JevProtocolError(f"{point_id}: 校准 margin 须在 [0, 1]")
        return self


@dataclasses.dataclass(frozen=True)
class FusedPoint:
    """融合结果：语义分布 + 置信 + margin + 逐探针分布（台账可审计）。"""

    point_id: str
    distribution: dict[str, float]
    confidence: float
    margin: float
    probes: dict[str, dict[str, float]]

    @property
    def value(self) -> str:
        return max(self.distribution, key=self.distribution.__getitem__)


def _point_probes(point) -> tuple[ProbeSpec, ...]:
    """点自身探针优先；未声明则用点定义构成单探针（兼容 AR001）。"""
    if point.probes:
        return tuple(spec.validated(point.id) for spec in point.probes)
    options = tuple(point.options)
    return (
        ProbeSpec(
            name="default",
            question_type=point.question_type,
            question=point.question,
            options=options,
            weight=1.0,
        ).validated(point.id),
    )


def _permutations_for(point) -> int:
    count = getattr(point, "permutations", 1)
    if not isinstance(count, int) or isinstance(count, bool):
        raise JevProtocolError(f"{point.id}: permutations 须为整数")
    if count < 1 or count > MAX_PERMUTATIONS:
        raise JevProtocolError(
            f"{point.id}: permutations 须在 [1, {MAX_PERMUTATIONS}]，实际 {count}"
        )
    if point.question_type != "choice" and count > 1:
        raise JevProtocolError(f"{point.id}: 仅 choice 型支持置换（score/noul 有序语义）")
    return count


def _build_question(spec: ProbeSpec, order: tuple[str, ...]) -> Question:
    if spec.question_type == "choice":
        return ChoiceQuestion(
            choices={label: None for label in order}, instructions=spec.question
        )
    if spec.question_type == "score":
        return ScoreQuestion(criteria=list(order), instructions=spec.question)
    return NoulQuestion(instructions=spec.question)


def plan_questions(
    points: Sequence, *, seed: int = 0
) -> tuple[dict[str, Question], list[ProbePlan]]:
    """为一组决策点生成单次批量评估的问题集合与逐点计划。"""
    questions: dict[str, Question] = {}
    plans: list[ProbePlan] = []
    for point in points:
        specs = _point_probes(point)
        permutations = _permutations_for(point)
        point_questions: dict[str, Question] = {}
        permuted: dict[str, tuple[str, ...]] = {}
        probe_of: dict[str, str] = {}
        for probe_index, spec in enumerate(specs):
            base_order = spec.options
            seen_orders: set[tuple[str, ...]] = set()
            produced = 0
            attempt = 0
            while produced < permutations and attempt < 64:
                if attempt == 0:
                    order = base_order
                else:
                    rng = random.Random(
                        f"{seed}:{point.id}:{spec.name}:{attempt}"
                    )
                    shuffled = list(base_order)
                    rng.shuffle(shuffled)
                    order = tuple(shuffled)
                attempt += 1
                if order in seen_orders:
                    continue  # 去重：同一顺序不重复评估
                seen_orders.add(order)
                if probe_index == 0 and produced == 0:
                    qid = point.id
                else:
                    qid = f"{point.id}~{spec.name}~{produced}"
                if qid in questions:
                    raise JevProtocolError(f"探针 qid 冲突: {qid}")
                questions[qid] = _build_question(spec, order)
                permuted[qid] = tuple(order)
                probe_of[qid] = spec.name
                point_questions[qid] = questions[qid]
                produced += 1
        plans.append(
            ProbePlan(
                point_id=point.id,
                questions=point_questions,
                permuted=permuted,
                probe_of=probe_of,
            )
        )
    return questions, plans


def _answer_distribution(
    qid: str, answer: Answer, order: Sequence[str]
) -> dict[str, float]:
    """答案 → 语义选项分布（置换后按选项名归并）。"""
    if isinstance(answer, ChoiceAnswer):
        dist = {label: float(answer.probabilities.get(label, 0.0)) for label in order}
    elif isinstance(answer, ScoreAnswer):
        raw = answer.probabilities
        dist = {label: float(raw.get(str(index), 0.0)) for index, label in enumerate(order)}
    else:
        raise JevProtocolError(f"{qid}: 未知答案类型 {type(answer).__name__}")
    total = sum(dist.values())
    if total <= 0:
        # 全零（模型极不可能给出但须防御）→ 均匀分布
        n = max(len(dist), 1)
        return {label: 1.0 / n for label in dist}
    return {label: value / total for label, value in dist.items()}


def _noul_distribution(qid: str, answer) -> dict[str, float]:
    p = float(answer.noul)
    if not 0.0 <= p <= 1.0:
        raise JevProtocolError(f"{qid}: noul 概率越界: {p}")
    return {"true": p, "false": 1.0 - p}


def _temperature_scale(dist: dict[str, float], temperature: float) -> dict[str, float]:
    if abs(temperature - 1.0) < 1e-12:
        return dict(dist)
    powered = {label: max(value, EPS) ** (1.0 / temperature) for label, value in dist.items()}
    total = sum(powered.values())
    return {label: value / total for label, value in powered.items()}


def fuse_point(
    plan: ProbePlan,
    answers: Mapping[str, Answer],
    specs: Sequence[ProbeSpec],
    calibration: PointCalibration | None = None,
) -> FusedPoint | None:
    """融合单点全部探针/置换答案；全部缺失 → None（交上层安全侧回退）。"""
    per_probe: dict[str, list[dict[str, float]]] = {spec.name: [] for spec in specs}
    for qid in plan.qids:
        answer = answers.get(qid)
        if answer is None:
            continue
        probe_name = plan.probe_of[qid]
        spec = next(s for s in specs if s.name == probe_name)
        if spec.question_type == "noul":
            dist = _noul_distribution(qid, answer)
        else:
            dist = _answer_distribution(qid, answer, plan.permuted[qid])
        per_probe[probe_name].append(dist)

    weight_by_probe = {spec.name: spec.weight for spec in specs}
    temperature = calibration.temperature if calibration else 1.0

    averaged: dict[str, dict[str, float]] = {}
    for name, dists in per_probe.items():
        if not dists:
            continue
        labels = dists[0].keys()
        merged = {label: 0.0 for label in labels}
        for dist in dists:
            for label in labels:
                merged[label] += dist.get(label, 0.0)
        averaged[name] = {label: value / len(dists) for label, value in merged.items()}

    if not averaged:
        return None

    # 语义标签全集：探针选项并集 ∪ 观测到的标签（异构探针可只覆盖子集，
    # 如 intent 的 binary 探针只有 decision/complex）
    labels: list[str] = []
    for spec in specs:
        for label in spec.options:
            if label not in labels:
                labels.append(label)
    for dist in averaged.values():
        for label in dist:
            if label not in labels:
                labels.append(label)

    # 对数意见池（按标签的加权平均）：仅由支持该标签的探针贡献，
    # 避免「探针不支持某选项」被当作极低概率惩罚
    log_scores: dict[str, float] = {}
    support: dict[str, float] = {label: 0.0 for label in labels}
    for label in labels:
        log_scores[label] = 0.0
    for name, dist in averaged.items():
        scaled = _temperature_scale(dist, temperature)
        weight = weight_by_probe[name]
        for label, value in scaled.items():
            log_scores[label] += weight * math.log(max(value, EPS))
            support[label] += weight
    pooled: dict[str, float] = {}
    for label in labels:
        if support[label] <= 0:
            pooled[label] = EPS  # 无探针覆盖 → 极小概率（仍出现在分布中）
        else:
            pooled[label] = math.exp(log_scores[label] / support[label])
    total = sum(pooled.values())
    distribution = {label: value / total for label, value in pooled.items()}
    ordered = sorted(distribution.values(), reverse=True)
    confidence = ordered[0]
    if len(ordered) > 1:
        margin = ordered[0] - ordered[1]
    else:
        margin = 0.0
    return FusedPoint(
        point_id=plan.point_id,
        distribution=distribution,
        confidence=confidence,
        margin=margin,
        probes=averaged,
    )


__all__ = [
    "EPS",
    "MAX_PERMUTATIONS",
    "FusedPoint",
    "PointCalibration",
    "ProbePlan",
    "ProbeSpec",
    "fuse_point",
    "plan_questions",
]
