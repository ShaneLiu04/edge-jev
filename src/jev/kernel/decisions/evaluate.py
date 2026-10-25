"""阶段批量评估（AR002 重构：自 server/chat.py 迁入 kernel，消除 bench→server 反向依赖）。

单次 systemone 前向评估一个 stage 的全部决策点（含多探针 × 置换），
逐点融合出 value/confidence/margin，并把校准阈值覆盖写入 Decision.tau。
小脑缺失/异常 → 逐点安全侧 fallback（保持 AR001 语义）。
"""

from __future__ import annotations

import dataclasses
import logging
import time
from collections.abc import Sequence

from jev.errors import JevError, JevProtocolError
from jev.kernel.cerebellum.base import Answer, CerebellumBackend
from jev.kernel.decisions.calibration import Calibration
from jev.kernel.decisions.framework import Decision, DecisionPoint
from jev.kernel.decisions.probes import (
    FusedPoint,
    PointCalibration,
    _point_probes,
    fuse_point,
    plan_questions,
)

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class StageEvaluation:
    decisions: list[Decision]
    answers: dict[str, Answer]
    latency_ms: float
    fused: dict[str, FusedPoint]


def fallback_point(point: DecisionPoint, reason: str) -> Decision:
    """安全侧回退：取点定义 fallback_action，置信 0（applied 必落 fallback）。"""
    return Decision(
        point_id=point.id,
        stage=point.stage,
        mode=point.mode,
        value=point.fallback_action,
        confidence=0.0,
        from_fallback=True,
        reason=reason,
    )


def evaluate_stage(
    cerebellum: CerebellumBackend | None,
    points: Sequence[DecisionPoint],
    state: str,
    *,
    seed: int = 0,
    calibration: Calibration | None = None,
) -> StageEvaluation:
    """同 stage 点合并为单次小脑批量前向；多探针/置换/校准在此完成。"""
    if not points:
        return StageEvaluation([], {}, 0.0, {})
    if cerebellum is None:
        return (
            StageEvaluation(
                [fallback_point(point, "小脑后端未配置") for point in points], {}, 0.0, {}
            )
        )

    try:
        questions, plans = plan_questions(points, seed=seed)
    except JevProtocolError:
        raise
    started = time.perf_counter()
    try:
        response = cerebellum.system_one(state, questions)
    except JevError as exc:
        logger.warning(
            "小脑评估失败，%d 个 %s 点安全侧回退: %s",
            len(points),
            points[0].stage.value,
            exc,
        )
        return (
            StageEvaluation(
                [fallback_point(point, f"小脑不可用: {exc}") for point in points],
                {},
                (time.perf_counter() - started) * 1000,
                {},
            )
        )
    latency_ms = (time.perf_counter() - started) * 1000

    decisions: list[Decision] = []
    answers: dict[str, Answer] = {}
    fused_by_point: dict[str, FusedPoint] = {}
    for point, plan in zip(points, plans, strict=True):
        # 原始答案保留在 answers（键为 qid），兼容既有调用方对 point.id 的取值
        for qid in plan.qids:
            if qid in response.answers:
                answers[qid] = response.answers[qid]
        specs = _point_probes(point)
        point_calibration: PointCalibration | None = None
        if calibration is not None:
            point_calibration = calibration.for_point(point.id, point.question_type)
        fused = fuse_point(plan, response.answers, specs, point_calibration)
        if fused is None:
            decisions.append(fallback_point(point, "小脑响应缺失该点全部探针答案"))
            continue
        fused_by_point[point.id] = fused
        decisions.append(
            Decision(
                point_id=point.id,
                stage=point.stage,
                mode=point.mode,
                value=fused.value,
                confidence=fused.confidence,
                margin=fused.margin,
                probes=dict(fused.probes),
                tau=point_calibration.tau if point_calibration else None,
            )
        )
    return StageEvaluation(decisions, answers, latency_ms, fused_by_point)


__all__ = ["StageEvaluation", "evaluate_stage", "fallback_point"]
