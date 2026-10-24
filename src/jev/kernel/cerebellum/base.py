"""小脑后端抽象契约（design.md §4.3.5）。

TypeSafe System One 线协议三型问题/答案 + 置信度公式（对齐
TypeSafe 参考适配器与 kev README 同款公式）。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ChoiceQuestion:
    """选择型问题：在命名选项上给出概率分布（描述可空，对齐 TypeSafe criteria）。"""

    choices: dict[str, str | None]
    instructions: str


@dataclass
class NoulQuestion:
    """判断型问题：0–1 概率；criteria 可选携带 true/false 选项描述。"""

    instructions: str
    criteria: dict[str, str] | None = None


@dataclass
class ScoreQuestion:
    """评分型问题：有序等级文本 + 概率分布（等级索引为键）。"""

    criteria: list[str]
    instructions: str


Question = ChoiceQuestion | NoulQuestion | ScoreQuestion


@dataclass
class ChoiceAnswer:
    choice: str
    confidence: float
    probabilities: dict[str, float]
    type: str = "choice"


@dataclass
class NoulAnswer:
    noul: float
    confidence: float
    type: str = "noul"


@dataclass
class ScoreAnswer:
    """TypeSafe score 答案：score=期望等级 E[level]（浮点）；legend/probabilities
    以等级索引字符串为键；mode_level 为众数等级文本（决策门控取语义等级用）。"""

    score: float
    confidence: float
    legend: dict[str, str]
    probabilities: dict[str, float]
    type: str = "score"

    @property
    def mode_level(self) -> str:
        best_index = max(self.probabilities, key=self.probabilities.__getitem__)
        return self.legend[best_index]


Answer = ChoiceAnswer | NoulAnswer | ScoreAnswer


@dataclass
class SystemOneResponse:
    """system_one 单次前向多问的聚合响应。"""

    answers: dict[str, Answer]
    input_tokens: int
    truncated: bool = False


class CerebellumBackend(ABC):
    """小脑后端统一接口；实现须真实推理（约束 C1：禁 mock）。"""

    @abstractmethod
    def system_one(self, state: str, questions: dict[str, Question]) -> SystemOneResponse:
        """单次前向回答多个问题（批处理语义）。"""

    @abstractmethod
    def name(self) -> str:
        """后端标识（配置引用用）。"""

    @abstractmethod
    def warmup(self) -> None:
        """预热：跑一次真实推理，暴露加载期问题。"""


def choice_confidence(probabilities: dict[str, float]) -> float:
    """(p_max − 1/K)/(1 − 1/K)；K=1 退化为恒 1.0。"""
    k = len(probabilities)
    if k <= 1:
        return 1.0
    p_max = max(probabilities.values())
    return (p_max - 1 / k) / (1 - 1 / k)


def noul_confidence(p: float) -> float:
    """2·max(p, 1−p) − 1。"""
    return 2 * max(p, 1 - p) - 1


def score_confidence(probs: dict[str, float]) -> float:
    """max(0, 1 − E|level − mode|/D)。

    mode=概率最大的等级；D=均匀分布各等级到中位等级 m=(L−1)/2 的平均距离
    （L=3 时 D=2/3，L=2 时 D=0.5）。
    """
    levels = list(probs)
    if not levels:
        return 0.0
    mode = max(probs, key=probs.__getitem__)
    mode_idx = levels.index(mode)
    mid = (len(levels) - 1) / 2
    spread = sum(abs(i - mid) for i in range(len(levels))) / len(levels)
    if spread == 0:
        return 1.0
    expected_distance = sum(
        prob * abs(idx - mode_idx) for idx, (level, prob) in enumerate(probs.items())
    )
    return max(0.0, 1 - expected_distance / spread)
