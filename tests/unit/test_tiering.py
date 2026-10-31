"""AR018 GTB 纯逻辑测试：锚点、接地窗口、归一化与配置边界。"""

from __future__ import annotations

import pytest

from jev.errors import JevAssetError
from jev.kernel.cerebrum.tiering import (
    TierConfig,
    extract_anchors,
    grounded_verify,
    normalize_text,
    split_question_document,
)


def _messages(document: str, question: str) -> list[dict]:
    return [
        {"role": "system", "content": "只输出数字。"},
        {"role": "user", "content": f"{document}\n\n问题：{question}"},
    ]


DOCUMENT = (
    "第 1 段记录：K-1000 的校验码为 4815，该记录仅出现一次。\n\n"
    "第 2 段记录：K-1001 的校验码为 4852，该记录仅出现一次。\n\n"
    "第 3 段记录：K-1002 的校验码为 4889，该记录仅出现一次。"
)


def test_grounded_answer_accepted_near_target_anchor():
    result = grounded_verify(
        _messages(DOCUMENT, "K-1001 的校验码是多少？"),
        "4852",
    )
    assert result.accepted is True
    assert result.anchor == "K-1001"


def test_answer_from_other_anchor_rejected():
    result = grounded_verify(
        _messages(DOCUMENT, "K-1001 的校验码是多少？"),
        "4815",
    )
    assert result.accepted is False
    assert result.reason == "answer_not_in_anchor_window"


def test_question_without_anchor_rejected():
    result = grounded_verify(
        _messages("一段没有编号的文档。", "请总结这段文档。"),
        "摘要",
    )
    assert result.accepted is False


def test_empty_and_too_long_answers_rejected():
    assert grounded_verify(_messages(DOCUMENT, "K-1000？"), "  ").reason == "empty_answer"
    long_answer = "x" * 65
    assert (
        grounded_verify(_messages(DOCUMENT, "K-1000？"), long_answer).reason
        == "answer_too_long"
    )


def test_no_document_rejected():
    result = grounded_verify(
        [{"role": "user", "content": "K-1000 的校验码是多少？"}],
        "4815",
    )
    assert result.accepted is False
    assert result.reason == "no_document_or_question"


def test_normalization_handles_space_case_and_punctuation():
    assert normalize_text(" 48 15。 ") == "4815"
    assert normalize_text("Code-ABCD") == "codeabcd"
    result = grounded_verify(
        _messages(DOCUMENT, "K-1000 的校验码是多少？"),
        " 48 15。 ",
        window=80,
    )
    assert result.accepted is True


def test_split_question_document_supports_explicit_marker_and_last_line():
    document, question = split_question_document("文档内容。\n\n问题：K-1000？")
    assert document == "文档内容。"
    assert question == "K-1000？"
    document, question = split_question_document("第一行\n第二行问题")
    assert document == "第一行"
    assert question == "第二行问题"


def test_extract_anchors_covers_identifier_number_quote_and_english():
    anchors = extract_anchors('K-1005 与 "Alpha Zone" 的 2026 值是什么？')
    assert "K-1005" in anchors
    assert "Alpha Zone" in anchors
    assert "2026" in anchors


def test_tier_config_validation_and_defaults():
    assert TierConfig().enabled is False
    assert TierConfig().ground_window == 600
    assert TierConfig().max_answer_chars == 64
    with pytest.raises(ValueError):
        TierConfig(ground_window=0)
    with pytest.raises(ValueError):
        TierConfig(max_answer_chars=0)
    with pytest.raises(ValueError):
        TierConfig(small_gpu_layers=-1)


def test_kernel_tier_config_defaults_off_and_requires_asset(tmp_path):
    from jev.kernel.kernel import _build_tier_config

    assert _build_tier_config({}).enabled is False
    missing = tmp_path / "missing.gguf"
    with pytest.raises(JevAssetError):
        _build_tier_config(
            {
                "cerebrum": {
                    "tier_enabled": True,
                    "small_model": str(missing),
                }
            }
        )
    present = tmp_path / "small.gguf"
    present.write_bytes(b"not-a-real-model")
    config = _build_tier_config(
        {
            "cerebrum": {
                "tier_enabled": True,
                "small_model": str(present),
                "small_gpu_layers": 3,
                "tier_ground_window": 111,
                "tier_max_answer_chars": 22,
            }
        }
    )
    assert config.enabled is True
    assert config.small_gpu_layers == 3
    assert config.ground_window == 111
    assert config.max_answer_chars == 22
