"""决策点框架（design.md §4.1 决策 4 / §4.3.5）：注册表只认注册、三态
active/shadow/off、声明式 TOML + 编程注册双通道、tau 门控安全侧默认。

评估走真实小脑后端（CerebellumBackend.system_one 单问）；门控语义：
confidence >= tau 采信小脑答案，否则取 fallback_action（安全侧默认）。
"""

import dataclasses
import logging
import tomllib
from enum import Enum
from pathlib import Path
from typing import Any

from jev.errors import JevAssetError, JevProtocolError
from jev.kernel.cerebellum.base import (
    Answer,
    CerebellumBackend,
    ChoiceAnswer,
    ChoiceQuestion,
    NoulAnswer,
    NoulQuestion,
    Question,
    ScoreAnswer,
    ScoreQuestion,
)

logger = logging.getLogger(__name__)

_QUESTION_TYPES = ("choice", "noul", "score")


class Stage(Enum):
    """决策点在生成流水线中的阶段。"""

    PRE = "PRE"
    MID = "MID"
    POST = "POST"


class Mode(Enum):
    """三态：active 生效于控制流 / shadow 只记录不干预 / off 完全跳过。"""

    ACTIVE = "active"
    SHADOW = "shadow"
    OFF = "off"


@dataclasses.dataclass(frozen=True)
class DecisionPoint:
    id: str
    stage: Stage
    question: str
    tau: float
    fallback_action: str
    question_type: str = "choice"
    options: tuple[str, ...] = ()
    mode: Mode = Mode.ACTIVE


@dataclasses.dataclass
class Decision:
    point_id: str
    stage: Stage
    mode: Mode
    value: str | float
    confidence: float
    from_fallback: bool = False
    reason: str | None = None

    def applied_value(self, point: DecisionPoint) -> str | float:
        """tau 门控：置信达阈值采信小脑答案，否则安全侧默认。"""
        if self.confidence >= point.tau:
            return self.value
        return point.fallback_action


class DecisionRegistry:
    """决策点注册表：声明序保持、同 id 编程覆盖、运行态三态切换。"""

    def __init__(self) -> None:
        self._points: dict[str, DecisionPoint] = {}

    @property
    def ids(self):
        return self._points.keys()

    def register(self, point: DecisionPoint) -> None:
        """注册/覆盖决策点（同 id 覆盖不换位）；非法声明抛 JevProtocolError。"""
        point = _normalize(point)
        _validate(point)
        self._points[point.id] = point
        logger.debug("决策点注册: %s（%s/%s）", point.id, point.stage.value, point.mode.value)

    def get(self, point_id: str) -> DecisionPoint:
        point = self._points.get(point_id)
        if point is None:
            known = ", ".join(self._points) or "(注册表为空)"
            raise JevProtocolError(f"未知决策点: {point_id}（已注册: {known}）")
        return point

    def set_mode(self, point_id: str, mode: Mode) -> None:
        if not isinstance(mode, Mode):
            raise JevProtocolError(f"非法 mode: {mode!r}（合法: ACTIVE/SHADOW/OFF）")
        self._points[point_id] = dataclasses.replace(self.get(point_id), mode=mode)

    def by_stage(self, stage: Stage) -> list[DecisionPoint]:
        return [point for point in self._points.values() if point.stage == stage]

    def active(self, stage: Stage) -> list[DecisionPoint]:
        return [p for p in self.by_stage(stage) if p.mode is Mode.ACTIVE]

    def shadow(self, stage: Stage) -> list[DecisionPoint]:
        return [p for p in self.by_stage(stage) if p.mode is Mode.SHADOW]

    @classmethod
    def from_toml(cls, path: str | Path) -> "DecisionRegistry":
        """解析 [[point]] 声明式配置；与 register 同一校验，重复 id 视为配置错误。"""
        path = Path(path)
        if not path.is_file():
            raise JevAssetError(f"决策点配置文件不存在: {path}")
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as exc:
            raise JevProtocolError(f"决策点配置不是合法 TOML（{path}）: {exc}") from exc
        tables = data.get("point", [])
        if not isinstance(tables, list) or not tables:
            raise JevProtocolError(f"决策点配置缺少 [[point]] 声明（{path}）")
        registry = cls()
        seen: set[str] = set()
        for table in tables:
            point = _point_from_table(table)
            if point.id in seen:
                raise JevProtocolError(f"决策点配置重复 id: {point.id}（{path}）")
            seen.add(point.id)
            registry.register(point)
        return registry


# ----------------------------------------------------------------------
# 点定义校验与构造
# ----------------------------------------------------------------------


def _normalize(point: DecisionPoint) -> DecisionPoint:
    """options 统一为 tuple（TOML/调用方可能传 list）。"""
    if not isinstance(point.options, tuple):
        return dataclasses.replace(point, options=tuple(point.options))
    return point


def _validate(point: DecisionPoint) -> None:
    if not isinstance(point.id, str) or not point.id:
        raise JevProtocolError(f"决策点 id 非法: {point.id!r}")
    if not isinstance(point.stage, Stage):
        raise JevProtocolError(f"{point.id}: stage 非法: {point.stage!r}（合法: PRE/MID/POST）")
    if not isinstance(point.mode, Mode):
        raise JevProtocolError(f"{point.id}: mode 非法: {point.mode!r}（合法: ACTIVE/SHADOW/OFF）")
    if not isinstance(point.question, str) or not point.question:
        raise JevProtocolError(f"{point.id}: question 必选且须为非空字符串")
    if not isinstance(point.tau, (int, float)) or isinstance(point.tau, bool):
        raise JevProtocolError(f"{point.id}: tau 须为数值，实际: {point.tau!r}")
    if not 0 < point.tau <= 1:
        raise JevProtocolError(f"{point.id}: tau 须在 (0, 1]，实际: {point.tau!r}")
    if point.question_type not in _QUESTION_TYPES:
        raise JevProtocolError(
            f"{point.id}: question_type 非法: {point.question_type!r}（合法: choice/noul/score）"
        )
    if not all(isinstance(option, str) for option in point.options):
        raise JevProtocolError(f"{point.id}: options 须为字符串数组")
    if point.question_type == "noul":
        if point.options:
            raise JevProtocolError(f"{point.id}: noul 问不携带 options")
        return
    if len(point.options) < 2:
        raise JevProtocolError(
            f"{point.id}: {point.question_type} 问 options 至少 2 项，实际: {list(point.options)}"
        )
    if point.fallback_action not in point.options:
        raise JevProtocolError(
            f"{point.id}: fallback_action {point.fallback_action!r} "
            f"须在 options 内: {list(point.options)}"
        )


def _point_from_table(table: Any) -> DecisionPoint:
    if not isinstance(table, dict):
        raise JevProtocolError(f"[[point]] 声明须为表，实际: {type(table).__name__}")
    point_id = table.get("id")
    if not isinstance(point_id, str) or not point_id:
        raise JevProtocolError(f"[[point]] id 必选且须为非空字符串，实际: {point_id!r}")

    stage_raw = table.get("stage")
    try:
        stage = Stage[str(stage_raw).upper()]
    except (KeyError, TypeError, ValueError):
        raise JevProtocolError(
            f"{point_id}: stage 非法: {stage_raw!r}（合法: PRE/MID/POST）"
        ) from None

    mode_raw = table.get("mode", "active")
    try:
        mode = Mode[str(mode_raw).upper()]
    except (KeyError, TypeError, ValueError):
        raise JevProtocolError(
            f"{point_id}: mode 非法: {mode_raw!r}（合法: active/shadow/off）"
        ) from None

    return DecisionPoint(
        id=point_id,
        stage=stage,
        question=table.get("question", ""),
        tau=table.get("tau", 0),
        fallback_action=table.get("fallback_action", ""),
        question_type=table.get("question_type", ""),
        options=tuple(table.get("options", ())),
        mode=mode,
    )


# ----------------------------------------------------------------------
# 评估与映射
# ----------------------------------------------------------------------


def to_question(point: DecisionPoint) -> Question:
    """决策点 → 小脑三型问题（instructions=问题文本；choice 选项渲染纯标签）。"""
    if point.question_type == "choice":
        return ChoiceQuestion(
            choices={label: None for label in point.options},
            instructions=point.question,
        )
    if point.question_type == "score":
        return ScoreQuestion(criteria=list(point.options), instructions=point.question)
    if point.question_type == "noul":
        return NoulQuestion(instructions=point.question)
    raise JevProtocolError(
        f"{point.id}: question_type 非法: {point.question_type!r}（合法: choice/noul/score）"
    )


def evaluate_point(
    backend: CerebellumBackend, point: DecisionPoint, state: str
) -> Decision:
    """真实小脑单问评估；答案三型分别取值与置信。"""
    response = backend.system_one(state, {point.id: to_question(point)})
    answer: Answer = response.answers[point.id]
    if isinstance(answer, ChoiceAnswer):
        value, confidence = answer.choice, answer.confidence
    elif isinstance(answer, ScoreAnswer):
        # 语义值取众数等级（期望等级 E[level] 为线协议值，门控比较需等级名）
        value, confidence = answer.mode_level, answer.confidence
    elif isinstance(answer, NoulAnswer):
        value, confidence = answer.noul, answer.confidence
    else:
        raise JevProtocolError(f"{point.id}: 未知答案类型: {type(answer).__name__}")
    return Decision(
        point_id=point.id,
        stage=point.stage,
        mode=point.mode,
        value=value,
        confidence=confidence,
    )
