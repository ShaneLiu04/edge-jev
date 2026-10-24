"""Laya 序列构造纯逻辑（receptron/laya sequence.ts 的 Python 端口）。

checkpoint rl_common.build_sequence 的模型无关实现：选项渲染、序列布局、
marker 位置、温度分桶与 clamp。与 ONNX 会话解耦，可独立单测。

序列布局（与训练一致）：
    [CLS] <type> question: 指令 [SEP] ⟨mask⟩选项0 ⟨mask⟩选项1 … [SEP] state [SEP]
marker=各选项的 ⟨mask⟩ token 位；qtype: choice=0 / score=1 / noul=2。
温度按 (qtype, 选项数分桶) 查 temperature_by_options，回退 temperature[qtype]，
clamp 到 [0.5, 5]（防非法 checkpoint 温度把 softmax 变 NaN）。
"""

import math
from dataclasses import dataclass

from jev.errors import JevProtocolError
from jev.kernel.cerebellum.base import (
    ChoiceQuestion,
    NoulQuestion,
    Question,
    ScoreQuestion,
)

QTYPES = {"choice": 0, "score": 1, "noul": 2}
QTYPE_NAMES = ("choice", "score", "noul")

# laya mask token 字面量（ModernBERT added token id 50284；用码点构造规避
# 工具链对括号型 token 的显示替换，字节恒为 5b4d41534b5d）
MASK_TOKEN = chr(91) + chr(77) + chr(65) + chr(83) + chr(75) + chr(93)

TEMP_MIN = 0.5
TEMP_MAX = 5.0

_NOUL_FALSE_DEFAULT = "no, the statement does not hold"
_NOUL_TRUE_DEFAULT = "yes, the statement holds"

_OPTION_TEXT_TOKEN_CAP = 48  # 单选项编码上限（token 数）


@dataclass(frozen=True)
class SpecialIds:
    """tokenizer 特殊 token id 与 mask token 文本。"""

    cls: int
    sep: int
    mask: int
    pad: int
    mask_token: str = MASK_TOKEN


@dataclass(frozen=True)
class BuiltSequence:
    """构造结果：token 序列、各选项 marker 位置、state 是否被截断。"""

    ids: list[int]
    markers: list[int]
    state_truncated: bool


def qtype_index(question_type: str) -> int:
    try:
        return QTYPES[question_type]
    except KeyError:
        raise JevProtocolError(
            f"未知问题类型: {question_type!r}（合法: choice/score/noul）"
        ) from None


def scrub(text: str, mask_token: str) -> str:
    """清除用户文本中的字面 mask token——防注入伪 marker 位。"""
    return text.replace(mask_token, " ")


def render_options(question: Question) -> list[str]:
    """三型问题 → 选项文本（label 序）。noul 恒为 [false, true]，p[1] 即 noul 概率。"""
    if isinstance(question, ChoiceQuestion):
        return [
            f"{label}: {desc}" if desc is not None else label
            for label, desc in question.choices.items()
        ]
    if isinstance(question, ScoreQuestion):
        return [f"level {index}: {level}" for index, level in enumerate(question.criteria)]
    if isinstance(question, NoulQuestion):
        criteria = question.criteria or {}
        false_text = criteria.get("false") or _NOUL_FALSE_DEFAULT
        true_text = criteria.get("true") or _NOUL_TRUE_DEFAULT
        return [f"false: {false_text}", f"true: {true_text}"]
    raise JevProtocolError(f"未知问题类型: {type(question).__name__}")


def _question_type(question: Question) -> str:
    if isinstance(question, ChoiceQuestion):
        return "choice"
    if isinstance(question, ScoreQuestion):
        return "score"
    if isinstance(question, NoulQuestion):
        return "noul"
    raise JevProtocolError(f"未知问题类型: {type(question).__name__}")


def size_bucket(k: int) -> str:
    if k <= 2:
        return "2"
    if k <= 5:
        return "3-5"
    if k <= 10:
        return "6-10"
    return "11+"


def temp_bucket(question_type: str, k: int) -> str:
    return f"{question_type}:{size_bucket(k)}"


def _is_finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def clamp_temperature(value: object, fallback: float = 1.0) -> float:
    """有限温度 clamp 到 [TEMP_MIN, TEMP_MAX]；非法值用 fallback（防 NaN 传播）。"""
    t = value if _is_finite_number(value) else fallback
    return min(TEMP_MAX, max(TEMP_MIN, t))


def temperature_for(config: dict, question_type: str, k: int) -> float:
    """桶温度优先，回退 qtype 基准，再回退 1；立即 clamp。"""
    bucket_value = (config.get("temperature_by_options") or {}).get(temp_bucket(question_type, k))
    base_list = config.get("temperature") or []
    qtype_index = QTYPES.get(question_type, -1)
    base_value = base_list[qtype_index] if 0 <= qtype_index < len(base_list) else None
    if _is_finite_number(bucket_value):
        return clamp_temperature(bucket_value)
    if _is_finite_number(base_value):
        return clamp_temperature(base_value)
    return clamp_temperature(1.0)


def build_sequence(
    encode,
    ids: SpecialIds,
    state: str,
    question: Question,
    max_len: int,
    head_max_len: int,
    state_ids: list[int] | None = None,
) -> BuiltSequence:
    """构造单问序列（[CLS] head [SEP] ⟨mask⟩选项… [SEP] state [SEP]）。

    string state 截断保前部（训练 truncate_left 语义面向会话数组 state）；
    选项过多/过长时按 head_max_len 预算均匀收缩选项编码。
    state_ids 可传入预算外预截断的 token（后端 truncate/refuse 语义先行处理时）。
    """
    question_type = _question_type(question)
    options = render_options(question)

    head_ids = encode(f"{question_type} question: {scrub(question.instructions, ids.mask_token)}")
    option_ids = [
        [ids.mask, *encode(f" {scrub(option, ids.mask_token)}")[:_OPTION_TEXT_TOKEN_CAP]]
        for option in options
    ]
    opt_budget = head_max_len - sum(len(opt) for opt in option_ids)
    if opt_budget < 16:
        # 预算不足：均匀收缩每个选项编码（保 ⟨mask⟩ + 前缀 token）
        per = max(4, (head_max_len - 16) // max(1, len(option_ids)))
        option_ids = [opt[:per] for opt in option_ids]
        opt_budget = head_max_len - sum(len(opt) for opt in option_ids)
    head_ids = head_ids[: max(8, opt_budget)]

    seq = [ids.cls, *head_ids, ids.sep]
    markers: list[int] = []
    for opt in option_ids:
        markers.append(len(seq))
        seq.extend(opt)
    seq.append(ids.sep)

    room = max(0, max_len - len(seq) - 1)
    if state_ids is None:
        state_ids = encode(scrub(state, ids.mask_token))
    kept = state_ids[:room]  # string state 保前部
    seq.extend(kept)
    seq.append(ids.sep)

    return BuiltSequence(
        ids=seq[:max_len],
        markers=[marker for marker in markers if marker < max_len],
        state_truncated=len(kept) < len(state_ids),
    )
