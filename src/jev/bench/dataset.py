"""实验数据集解析与客观判分（AR005 FR5-4）。

仅接受显式标注（gold）与固定 schema；不猜测、不调用模型判分。
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import yaml

from jev.errors import JevProtocolError


@dataclasses.dataclass(frozen=True)
class Gold:
    kind: str  # contains | regex | numeric
    value: str
    tolerance: float = 0.0


@dataclasses.dataclass(frozen=True)
class QualityQuestion:
    id: str
    prompt: str
    gold: Gold
    lang: str = "zh"
    category: str = "factual"


@dataclasses.dataclass(frozen=True)
class CacheItem:
    id: str
    text: str
    of: str | None = None


@dataclasses.dataclass(frozen=True)
class CacheSet:
    originals: tuple[CacheItem, ...]
    paraphrases: tuple[CacheItem, ...]
    negatives: tuple[CacheItem, ...]


def _read_yaml(path: Path | str, label: str) -> dict:
    file = Path(path)
    try:
        data = yaml.safe_load(file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise JevProtocolError(f"{label} 读取失败: {file}: {exc}") from exc
    if not isinstance(data, dict):
        raise JevProtocolError(f"{label} 顶层必须是映射: {file}")
    return data


def _parse_gold(raw: object, question_id: str) -> Gold:
    if not isinstance(raw, dict):
        raise JevProtocolError(f"gold 缺失/非法: {question_id}")
    kind = str(raw.get("type", "contains"))
    if kind not in {"contains", "regex", "numeric"}:
        raise JevProtocolError(f"gold.type 非法: {question_id}: {kind}")
    if "value" not in raw:
        raise JevProtocolError(f"gold.value 缺失: {question_id}")
    return Gold(
        kind=kind,
        value=str(raw["value"]),
        tolerance=float(raw.get("tolerance", 0.0)),
    )


def load_quality_set(path: Path | str) -> list[QualityQuestion]:
    data = _read_yaml(path, "质量集")
    rows = data.get("questions")
    if not isinstance(rows, list) or not rows:
        raise JevProtocolError("质量集 questions 必须是非空列表")
    questions: list[QualityQuestion] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise JevProtocolError("质量集条目必须是映射")
        question_id = str(row.get("id", "")).strip()
        prompt = str(row.get("prompt", "")).strip()
        if not question_id or not prompt:
            raise JevProtocolError(f"质量集条目缺 id/prompt: {row!r}")
        if question_id in seen:
            raise JevProtocolError(f"质量集 id 重复: {question_id}")
        seen.add(question_id)
        questions.append(
            QualityQuestion(
                id=question_id,
                prompt=prompt,
                gold=_parse_gold(row.get("gold"), question_id),
                lang=str(row.get("lang", "zh")),
                category=str(row.get("category", "factual")),
            )
        )
    return questions


def load_cache_set(path: Path | str) -> CacheSet:
    data = _read_yaml(path, "缓存集")

    def _items(key: str) -> tuple[CacheItem, ...]:
        rows = data.get(key)
        if not isinstance(rows, list) or not rows:
            raise JevProtocolError(f"缓存集 {key} 必须是非空列表")
        items: list[CacheItem] = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("id") or not row.get("text"):
                raise JevProtocolError(f"缓存集 {key} 条目缺 id/text")
            items.append(
                CacheItem(
                    id=str(row["id"]),
                    text=str(row["text"]),
                    of=str(row["of"]) if row.get("of") is not None else None,
                )
            )
        return tuple(items)

    return CacheSet(
        originals=_items("originals"),
        paraphrases=_items("paraphrases"),
        negatives=_items("negatives"),
    )


def grade(answer: str, gold: Gold) -> bool:
    text = (answer or "").strip()
    if not text:
        return False
    if gold.kind == "contains":
        return gold.value.strip().lower() in text.lower()
    if gold.kind == "regex":
        try:
            return re.search(gold.value, text, flags=re.IGNORECASE) is not None
        except re.error as exc:
            raise JevProtocolError(f"gold.regex 非法: {gold.value!r}: {exc}") from exc
    numbers = re.findall(r"-?\d+(?:\.\d+)?", text)
    try:
        expected = float(gold.value)
    except ValueError as exc:
        raise JevProtocolError(f"gold.numeric 非法: {gold.value!r}") from exc
    return any(abs(float(value) - expected) <= gold.tolerance for value in numbers)


__all__ = [
    "CacheItem",
    "CacheSet",
    "Gold",
    "QualityQuestion",
    "grade",
    "load_cache_set",
    "load_quality_set",
]
