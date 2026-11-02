"""AR020 GEXA：锚点接地、唯一候选的确定性抽取式快答。

只处理"文档中已有唯一答案"的抽取型问题（numeric/date）。任何歧义、生成型
请求或无强锚点都返回 miss，由现有级联继续处理；不依赖模型/网络。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from jev.kernel.cerebrum.tiering import split_question_document

_IDENTIFIER_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,15}\d[A-Za-z0-9_-]*|\d{4,}")
_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])-?\d+(?:\.\d+)?(?![A-Za-z0-9_.-])"
)
_DATE_RE = re.compile(r"(?:19|20)\d{2}\s*[年/-]\s*\d{1,2}\s*[月/-]\s*\d{1,2}\s*日?")
_QUOTED_RE = re.compile(r"[\"'“”‘’]([^\"'“”‘’]{2,40})[\"'“”‘’]")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,8}")

_CUE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("checksum", ("校验码", "验证码")),
    ("date", ("日期", "时间", "何时", "哪天", "date", "when")),
    ("serial", ("序列号", "serial")),
    ("model", ("型号", "model")),
    ("count", ("数量", "总数", "多少件", "几个", "count", "total")),
    ("temperature", ("温度",)),
    ("humidity", ("湿度",)),
    ("amount", ("金额", "价格")),
    ("identifier", ("编号", "code", "number", "id")),
)

_NON_EXTRACTIVE = (
    "详细", "说明", "解释", "分析", "总结", "写", "生成", "列出", "至少",
    "字左右", "字以内", "多少字", "words", "explain", "describe", "write",
    "summar",
)
_GENERIC_NUMERIC_CUES = ("多少", "几", "how many")
_STOP_ANCHORS = {
    "问题", "多少", "什么", "以下", "一份", "仓库", "巡检", "记录", "只输出",
    "数字", "请问", "告诉", "是多少", "为什么", "怎么样", "是否", "如何",
}


def _has_cue(cue: str, text: str) -> bool:
    if cue.isascii() and cue.isalnum():
        pattern = rf"(?<![a-z0-9]){re.escape(cue)}(?![a-z0-9])"
        return re.search(pattern, text) is not None
    return cue in text


def _operation(question: str) -> str | None:
    if any(word in question for word in ("哪个", "哪一个", "哪条", "which")):
        max_words = ("更大", "较大", "最大", "最高", "larger", "largest")
        min_words = ("更小", "较小", "最小", "最低", "smaller", "smallest")
        if any(word in question for word in max_words):
            return "which_max"
        if any(word in question for word in min_words):
            return "which_min"
    operations = (
        ("sum", ("之和", "总和", "合计", "相加", "加起来", "sum", "total")),
        ("diff", ("相差", "差额", "之差", "difference", "差多少")),
        ("max", ("较大", "最大", "最高", "largest", "maximum")),
        ("min", ("较小", "最小", "最低", "smallest", "minimum")),
    )
    for name, cues in operations:
        if any(cue in question for cue in cues):
            return name
    return None


def _format_decimal(value: Decimal) -> str:
    if value == value.to_integral_value():
        return str(int(value))
    return format(value.normalize(), "f")


def _first_cue_end(sentence: str, cues: list[str]) -> int | None:
    lowered = sentence.lower()
    ends: list[int] = []
    for cue in cues:
        if cue.isascii() and cue.isalnum():
            pattern = rf"(?<![a-z0-9]){re.escape(cue)}(?![a-z0-9])"
            match = re.search(pattern, lowered)
            if match is not None:
                ends.append(match.end())
        else:
            index = lowered.find(cue)
            if index >= 0:
                ends.append(index + len(cue))
    return min(ends) if ends else None


@dataclass(frozen=True)
class ExtractiveConfig:
    enabled: bool = False
    min_document_chars: int = 200
    max_answer_chars: int = 64
    max_anchor_occurrences: int = 64
    quantitative_enabled: bool = False

    def __post_init__(self) -> None:
        if self.min_document_chars < 0:
            raise ValueError("extractive.min_document_chars 必须 >= 0")
        if self.max_answer_chars < 1:
            raise ValueError("extractive.max_answer_chars 必须 >= 1")
        if self.max_anchor_occurrences < 1:
            raise ValueError("extractive.max_anchor_occurrences 必须 >= 1")


@dataclass(frozen=True)
class ExtractionResult:
    hit: bool
    answer: str = ""
    kind: str = ""
    anchor: str = ""
    reason: str = "miss"

    @property
    def header_value(self) -> str:
        return f"hit:{self.kind}" if self.hit else f"miss:{self.reason}"


class GroundedExtractor:
    """保守型抽取器；命中条件全部满足才返回，否则 miss。"""

    def __init__(self, config: ExtractiveConfig) -> None:
        self._config = config

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def answer(self, messages: list[dict[str, Any]]) -> ExtractionResult:
        if not self._config.enabled:
            return ExtractionResult(False, reason="disabled")
        text = self._last_user(messages)
        document, question = split_question_document(text)
        if len(document) < self._config.min_document_chars or not question:
            return ExtractionResult(False, reason="no-doc")
        lowered = question.lower()
        if any(word in lowered for word in _NON_EXTRACTIVE):
            return ExtractionResult(False, reason="non-extractive")

        arithmetic = self._arithmetic(document, question, lowered)
        if arithmetic is not None:
            return arithmetic

        kind, cues = self._answer_type(lowered)
        if kind is None:
            return ExtractionResult(False, reason="no-type")
        anchors = self._anchors(question)
        if not anchors:
            return ExtractionResult(False, reason="no-anchor")

        candidates: set[str] = set()
        occurrences = 0
        for anchor in anchors:
            for match in re.finditer(re.escape(anchor), document, flags=re.IGNORECASE):
                occurrences += 1
                if occurrences > self._config.max_anchor_occurrences:
                    return ExtractionResult(False, reason="too-many-anchors")
                left, _right, sentence = self._sentence_at(
                    document, match.start(), match.end()
                )
                if not any(_has_cue(cue, sentence.lower()) for cue in cues):
                    continue
                values = self._values(
                    kind, sentence, left, match.start(), match.end(), cues
                )
                if not values:
                    return ExtractionResult(False, reason="no-candidate")
                candidates.update(values)
                if len(candidates) > 1:
                    return ExtractionResult(False, reason="ambiguous")
        if not candidates:
            return ExtractionResult(False, reason="no-candidate")
        value = next(iter(candidates))
        if len(value) > self._config.max_answer_chars:
            return ExtractionResult(False, reason="answer-too-long")
        return ExtractionResult(True, answer=value, kind=kind, anchor=anchors[0], reason="hit")

    def _arithmetic(
        self, document: str, question: str, lowered: str
    ) -> ExtractionResult | None:
        if not self._config.quantitative_enabled:
            return None
        operation = _operation(lowered)
        if operation is None:
            return None
        kind, cues = self._answer_type(lowered)
        if kind != "numeric" or not cues:
            return ExtractionResult(False, reason="arithmetic-no-cue")
        anchors = self._anchors(question)
        if len(anchors) < 2:
            return ExtractionResult(False, reason="arithmetic-anchors")
        values: list[tuple[str, Decimal]] = []
        for anchor in anchors:
            value = self._resolve_anchor(document, anchor, cues)
            if value is None:
                return ExtractionResult(False, reason="arithmetic-unresolved")
            try:
                values.append((anchor, Decimal(value)))
            except InvalidOperation:
                return ExtractionResult(False, reason="arithmetic-invalid")
        if operation == "sum":
            result = sum((value for _, value in values), Decimal(0))
            answer, answer_kind = _format_decimal(result), "numeric-sum"
        elif operation == "diff":
            if len(values) != 2:
                return ExtractionResult(False, reason="arithmetic-anchors")
            answer = _format_decimal(abs(values[0][1] - values[1][1]))
            answer_kind = "numeric-diff"
        elif operation in {"which_max", "which_min"}:
            pick = max if operation == "which_max" else min
            anchor, _value = pick(values, key=lambda item: item[1])
            answer, answer_kind = anchor, "id-max" if operation == "which_max" else "id-min"
        else:
            pick = max if operation == "max" else min
            _anchor, value = pick(values, key=lambda item: item[1])
            answer, answer_kind = _format_decimal(value), f"numeric-{operation}"
        if len(answer) > self._config.max_answer_chars:
            return ExtractionResult(False, reason="answer-too-long")
        return ExtractionResult(
            True,
            answer=answer,
            kind=answer_kind,
            anchor=",".join(anchor for anchor, _ in values),
            reason="hit",
        )

    def _resolve_anchor(
        self, document: str, anchor: str, cues: list[str]
    ) -> str | None:
        candidates: set[str] = set()
        occurrences = 0
        for match in re.finditer(re.escape(anchor), document, flags=re.IGNORECASE):
            occurrences += 1
            if occurrences > self._config.max_anchor_occurrences:
                return None
            left, _right, sentence = self._sentence_at(
                document, match.start(), match.end()
            )
            if not any(_has_cue(cue, sentence.lower()) for cue in cues):
                continue
            values = self._values(
                "numeric", sentence, left, match.start(), match.end(), cues
            )
            if not values:
                return None
            candidates.update(values)
            if len(candidates) > 1:
                return None
        return next(iter(candidates)) if len(candidates) == 1 else None

    @staticmethod
    def _last_user(messages: list[dict[str, Any]]) -> str:
        for message in reversed(messages):
            if message.get("role") == "user" and isinstance(message.get("content"), str):
                return message["content"]
        return ""

    @staticmethod
    def _answer_type(question: str) -> tuple[str | None, list[str]]:
        for name, group in _CUE_GROUPS:
            matched = [cue for cue in group if _has_cue(cue, question)]
            if matched:
                kind = "date" if name == "date" else "numeric"
                return kind, matched
        matched = [cue for cue in _GENERIC_NUMERIC_CUES if _has_cue(cue, question)]
        return ("numeric", matched) if matched else (None, [])

    @staticmethod
    def _anchors(question: str) -> list[str]:
        anchors: list[str] = []
        for match in _IDENTIFIER_RE.finditer(question):
            if len(match.group(0)) >= 4:
                anchors.append(match.group(0))
        anchors.extend(match.group(0) for match in _DATE_RE.finditer(question))
        anchors.extend(match.group(1) for match in _QUOTED_RE.finditer(question))
        if not anchors:
            anchors.extend(
                match.group(0)
                for match in _CJK_RE.finditer(question)
                if match.group(0) not in _STOP_ANCHORS
            )
        unique = sorted(set(anchors), key=len, reverse=True)
        return [
            anchor
            for anchor in unique
            if not any(anchor != other and anchor in other for other in unique)
        ]

    @staticmethod
    def _sentence_at(document: str, start: int, end: int) -> tuple[int, int, str]:
        left = max(document.rfind(ch, 0, start) for ch in "。！？\n") + 1
        rights = [document.find(ch, end) for ch in "。！？\n"]
        rights = [index for index in rights if index >= 0]
        right = min(rights) if rights else len(document)
        return left, right, document[left:right]

    @staticmethod
    def _values(
        kind: str,
        sentence: str,
        sentence_start: int,
        anchor_start: int,
        anchor_end: int,
        cues: list[str],
    ) -> list[str]:
        cue_end = _first_cue_end(sentence, cues)
        if cue_end is None:
            return []
        pattern = _DATE_RE if kind == "date" else _NUMBER_RE
        values: list[str] = []
        for match in pattern.finditer(sentence):
            absolute_start = sentence_start + match.start()
            absolute_end = sentence_start + match.end()
            if absolute_start < sentence_start + cue_end:
                continue
            overlaps = absolute_start < anchor_end and absolute_end > anchor_start
            if not overlaps:
                values.append(match.group(0))
        return values
