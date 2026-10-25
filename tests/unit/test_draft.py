"""AR002 T005：PRE 草稿注入与历史压缩（FR2-3）。

纯逻辑：不触后端；用真实内置注册表点定义验证 applied 值进入前缀。
"""

import pytest

from jev.kernel.cascade.draft import (
    DraftConfig,
    apply_draft,
    build_draft_prefix,
    compress_history,
    estimate_messages_tokens,
    estimate_tokens,
    summarize_message,
)
from jev.kernel.decisions.builtin import load_builtin_registry
from jev.kernel.decisions.framework import Decision, Mode, Stage

_REGISTRY = load_builtin_registry()


def _decision(point_id: str, value: str, confidence: float) -> Decision:
    point = _REGISTRY.get(point_id)
    return Decision(
        point_id=point_id,
        stage=point.stage,
        mode=Mode.ACTIVE,
        value=value,
        confidence=confidence,
    )


def _long_history(turns: int = 4) -> list[dict]:
    messages: list[dict] = []
    for index in range(turns):
        messages.append(
            {"role": "user", "content": f"第{index}轮问题：" + "细节描述" * 40}
        )
        messages.append(
            {"role": "assistant", "content": f"第{index}轮回答：" + "解释说明" * 40}
        )
    return messages


class TestDraftConfig:
    def test_default_is_disabled(self):
        config = DraftConfig()
        assert config.enabled is False
        assert config.keep_turns == 2


class TestTokenEstimate:
    def test_estimates_ceiling_quarter_chars(self):
        assert estimate_tokens("") == 0
        assert estimate_tokens("abcd") == 1
        assert estimate_tokens("abcde") == 2

    def test_messages_sum(self):
        messages = [{"role": "user", "content": "abcd"}, {"role": "assistant", "content": "efgh"}]
        assert estimate_messages_tokens(messages) == 2


class TestSummarize:
    def test_short_message_unchanged_and_copied(self):
        message = {"role": "user", "content": "短消息"}
        result = summarize_message(message, max_chars=10)
        assert result == message
        assert result is not message

    def test_long_message_truncated_with_ellipsis(self):
        result = summarize_message({"role": "user", "content": "字" * 200}, max_chars=120)
        assert len(result["content"]) == 121
        assert result["content"].endswith("…")


class TestCompressHistory:
    def test_keeps_last_n_turns_verbatim(self):
        messages = _long_history(turns=4)
        compressed = compress_history(messages, keep_turns=2)
        assert len(compressed) == len(messages)
        # 最后两轮（第 3、4 轮）原样保留
        assert compressed[4:] == messages[4:]
        # 更早轮次被压缩
        assert all(len(m["content"]) <= 121 for m in compressed[:4])

    def test_single_turn_untouched(self):
        messages = [{"role": "user", "content": "只有一个问题"}]
        assert compress_history(messages, keep_turns=2) == messages

    def test_rejects_non_positive_keep_turns(self):
        with pytest.raises(ValueError):
            compress_history(_long_history(1), keep_turns=0)

    def test_without_user_messages_all_summarized(self):
        messages = [{"role": "system", "content": "长" * 300}]
        compressed = compress_history(messages, keep_turns=2)
        assert len(compressed[0]["content"]) == 121


class TestDraftPrefix:
    def test_uses_applied_values_from_registry(self):
        decisions = [
            _decision("intent", "complex", 0.9),
            _decision("complexity", "high", 0.8),
        ]
        prefix = build_draft_prefix(decisions, _REGISTRY)
        assert prefix.startswith("系统预判：")
        assert "intent=complex" in prefix
        assert "complexity=high" in prefix

    def test_unknown_point_falls_back_to_raw_value(self):
        decision = Decision(
            point_id="not_in_registry",
            stage=Stage.PRE,
            mode=Mode.ACTIVE,
            value="raw",
            confidence=0.9,
        )
        prefix = build_draft_prefix([decision], _REGISTRY)
        assert "not_in_registry=raw" in prefix

    def test_empty_decisions_yield_empty_prefix(self):
        assert build_draft_prefix([], _REGISTRY) == ""

    def test_caps_items(self):
        decisions = [_decision("intent", "complex", 0.9) for _ in range(10)]
        prefix = build_draft_prefix(decisions, _REGISTRY, max_items=3)
        assert prefix.count("intent=") == 3


class TestApplyDraft:
    def test_prefix_prepended_as_system(self):
        messages = [{"role": "user", "content": "问题"}]
        result = apply_draft(messages, "系统预判：intent=complex", keep_turns=2)
        assert result[0] == {"role": "system", "content": "系统预判：intent=complex"}
        assert result[1:] == messages

    def test_no_prefix_only_compresses(self):
        messages = _long_history(turns=4)
        result = apply_draft(messages, "", keep_turns=2)
        assert all(m["role"] != "system" for m in result)
        assert len(result) == len(messages)

    def test_token_saving_at_least_fifteen_percent(self):
        messages = _long_history(turns=5)
        prefix = "系统预判：intent=complex；complexity=high"
        before = estimate_messages_tokens(messages)
        after = estimate_messages_tokens(apply_draft(messages, prefix, keep_turns=1))
        assert after <= before * 0.85

    def test_input_messages_not_mutated(self):
        messages = _long_history(turns=3)
        snapshot = [dict(m) for m in messages]
        apply_draft(messages, "前缀", keep_turns=1)
        assert messages == snapshot
