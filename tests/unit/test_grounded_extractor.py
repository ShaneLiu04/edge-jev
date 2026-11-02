from __future__ import annotations

from pathlib import Path

import pytest

from jev.bench.dataset import grade, load_quality_set
from jev.bench.generation import build_shared_generation
from jev.bench.needles import build_needles
from jev.bench.prefixset import build_shared_prefix
from jev.kernel.extract import ExtractiveConfig, GroundedExtractor

REPO = Path(__file__).resolve().parents[2]


def _extractor() -> GroundedExtractor:
    return GroundedExtractor(ExtractiveConfig(enabled=True, min_document_chars=200))


def _messages(prompt: str) -> list[dict[str, str]]:
    return [{"role": "user", "content": prompt}]


def test_shared_prefix_all_extracted_and_correct() -> None:
    questions = build_shared_prefix(12)
    hits = 0
    for question in questions:
        result = _extractor().answer(_messages(question.prompt))
        if result.hit:
            hits += 1
            assert grade(result.answer, question.gold), (question.id, result.answer)
    assert hits == 12


def test_needles_all_extracted_and_correct() -> None:
    questions = build_needles(30)
    hits = 0
    for question in questions:
        result = _extractor().answer(_messages(question.prompt))
        if result.hit:
            hits += 1
            assert grade(result.answer, question.gold), (question.id, result.answer)
    assert hits == 30


def test_quality_set_never_false_hits() -> None:
    questions = load_quality_set(REPO / "configs" / "quality-set.yaml")
    assert questions
    assert all(not _extractor().answer(_messages(q.prompt)).hit for q in questions)


def test_generation_requests_are_rejected() -> None:
    questions = build_shared_generation(8)
    assert questions
    for question in questions:
        result = _extractor().answer(_messages(question.prompt))
        assert not result.hit
        assert result.reason == "non-extractive"


def test_ambiguous_candidates_fall_back() -> None:
    document = (
        "第0段特别记录：编号 K-1000 的校验码是 4815，归档完成。\n\n"
        "第1段特别记录：编号 K-1000 的校验码是 9999，归档完成。\n\n"
        + "其他仓库巡检记录，未发现异常。" * 20
    )
    prompt = f"{document}\n\n问题：编号 K-1000 的校验码是多少？只输出数字。"
    result = _extractor().answer(_messages(prompt))
    assert not result.hit
    assert result.reason == "ambiguous"


def test_date_extraction() -> None:
    document = (
        "第0段记录：编号 K-2000 的入库日期为 2026 年 3 月 15 日，负责人为 A。"
        + "其他巡检内容。" * 30
    )
    prompt = f"{document}\n\n问题：编号 K-2000 的入库日期是哪天？只输出日期。"
    result = _extractor().answer(_messages(prompt))
    assert result.hit
    assert result.kind == "date"
    assert "2026" in result.answer


def test_disabled_and_short_document_miss() -> None:
    disabled = GroundedExtractor(ExtractiveConfig(enabled=False))
    result = disabled.answer(_messages(build_shared_prefix(1)[0].prompt))
    assert not result.hit and result.reason == "disabled"
    short = _extractor().answer(
        _messages("编号 K-1000 的校验码是多少？只输出数字。\n问题：编号 K-1000 的校验码")
    )
    assert not short.hit
    assert short.reason in {"no-doc", "no-candidate"}


def test_config_validation() -> None:
    with pytest.raises(ValueError):
        ExtractiveConfig(enabled=True, min_document_chars=-1)
    with pytest.raises(ValueError):
        ExtractiveConfig(enabled=True, max_answer_chars=0)
    with pytest.raises(ValueError):
        ExtractiveConfig(enabled=True, max_anchor_occurrences=0)


def test_chat_extractive_only_runs_on_full_route() -> None:
    from types import SimpleNamespace

    from jev.server.chat import _try_extractive

    context = SimpleNamespace(
        extractor=_extractor(),
        messages=_messages("hello"),
        request_id="r1",
    )
    assert _try_extractive(context, "light") is None
    assert _try_extractive(context, "full") is None


def test_kernel_builder_default_off_and_enabled() -> None:
    from jev.kernel.kernel import _build_extractor

    assert not _build_extractor({}).enabled
    enabled = _build_extractor(
        {
            "extractive": {
                "enabled": True,
                "min_document_chars": 100,
                "max_answer_chars": 32,
                "max_anchor_occurrences": 8,
            }
        }
    )
    assert enabled.enabled


def test_header_value_is_observable() -> None:
    result = _extractor().answer(_messages(build_shared_prefix(1)[0].prompt))
    assert result.hit
    assert result.header_value == "hit:numeric"
