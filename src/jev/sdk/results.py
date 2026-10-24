"""SDK 类型化结果对象（design.md §4.3.3，FR10：非裸 dict）。"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ChoiceResult:
    """choice 问结果：标签 + 置信 + 全选项分布。"""

    choice: str
    confidence: float
    probabilities: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class NoulResult:
    """noul 问结果：真值概率 [0,1]。"""

    noul: float
    confidence: float


@dataclass(frozen=True)
class ScoreResult:
    """score 问结果：期望等级 E[level]（浮点）+ 等级图 + 置信。"""

    score: float
    confidence: float
    legend: dict[str, str] = field(default_factory=dict)
    mode_level: str = ""
    probabilities: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ChatResult:
    """chat 结果：内容 + 级联元信息（路由/决策数/降级标注）。"""

    id: str
    content: str
    finish_reason: str
    route: str
    decisions: int
    degraded: bool
    usage: dict[str, int] = field(default_factory=dict)
