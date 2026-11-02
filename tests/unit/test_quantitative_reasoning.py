from __future__ import annotations

import pytest

from jev.bench.dataset import grade
from jev.bench.numeric_reasoning import build_numeric_reasoning
from jev.bench.prefixset import build_shared_prefix
from jev.kernel.extract import ExtractiveConfig, GroundedExtractor


def _extractor(*, quantitative: bool = True) -> GroundedExtractor:
    return GroundedExtractor(
        ExtractiveConfig(
            enabled=True,
            min_document_chars=200,
            quantitative_enabled=quantitative,
        )
    )


def _messages(prompt: str) -> list[dict[str, str]]:
    return [{"role": "user", "content": prompt}]


def test_numeric_reasoning_all_correct() -> None:
    questions = build_numeric_reasoning(12)
    assert len(questions) == 12
    for question in questions:
        result = _extractor().answer(_messages(question.prompt))
        assert result.hit, (question.id, result.reason)
        assert grade(result.answer, question.gold), (
            question.id,
            result.answer,
            question.gold.value,
        )


def test_quantitative_disabled_misses_composite_questions() -> None:
    for question in build_numeric_reasoning(6):
        result = _extractor(quantitative=False).answer(_messages(question.prompt))
        assert not result.hit
        assert result.reason == "ambiguous"


def test_sum_diff_max_min_and_which_kinds() -> None:
    results = [
        _extractor().answer(_messages(question.prompt))
        for question in build_numeric_reasoning(6)
    ]
    assert [result.kind for result in results] == [
        "numeric-sum",
        "numeric-diff",
        "numeric-max",
        "numeric-min",
        "id-max",
        "id-min",
    ]


def test_single_anchor_operation_falls_back() -> None:
    document = (
        "第0段特别记录：编号 K-1000 的校验码是 4815，归档完成。"
        + "其他巡检内容。" * 30
    )
    prompt = f"{document}\n\n问题：编号 K-1000 的校验码之和是多少？只输出数字。"
    result = _extractor().answer(_messages(prompt))
    assert not result.hit
    assert result.reason == "arithmetic-anchors"


def test_ambiguous_operand_falls_back() -> None:
    document = (
        "第0段特别记录：编号 K-1000 的校验码是 4815，归档完成。\n\n"
        "第1段特别记录：编号 K-1000 的校验码是 9999，归档完成。\n\n"
        "第2段特别记录：编号 K-1007 的校验码是 4889，归档完成。\n\n"
        + "其他巡检内容。" * 20
    )
    prompt = (
        f"{document}\n\n问题：编号 K-1000 和 K-1007 的校验码之和是多少？"
        "只输出数字。"
    )
    result = _extractor().answer(_messages(prompt))
    assert not result.hit
    assert result.reason == "arithmetic-unresolved"


def test_unsupported_arithmetic_falls_back() -> None:
    question = build_numeric_reasoning(1)[0]
    prompt = question.prompt.replace("之和", "的平均值")
    result = _extractor().answer(_messages(prompt))
    assert not result.hit


def test_gqra_does_not_change_single_fact_answer() -> None:
    question = build_shared_prefix(1)[0]
    result = _extractor().answer(_messages(question.prompt))
    assert result.hit
    assert result.kind == "numeric"
    assert grade(result.answer, question.gold)


def test_quantitative_config_is_part_of_extractive_config() -> None:
    assert not ExtractiveConfig().quantitative_enabled
    assert ExtractiveConfig(quantitative_enabled=True).quantitative_enabled
    with pytest.raises(ValueError):
        ExtractiveConfig(enabled=True, max_anchor_occurrences=0)
