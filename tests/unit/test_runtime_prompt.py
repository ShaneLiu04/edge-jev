"""AR003 T008/T012：prompt 单入口组装（记忆→草稿→prefill，FR4-1/FR4-3/FR5-1）。"""

import pytest

from jev.kernel.cache.memory import memory_tokens
from jev.kernel.decisions.builtin import load_builtin_registry
from jev.kernel.decisions.framework import Decision, Mode
from jev.kernel.runtime.prompt import PromptConfig, build_prompt

_REGISTRY = load_builtin_registry()


def _decision(point_id: str, value: str, confidence: float = 0.9) -> Decision:
    point = _REGISTRY.get(point_id)
    return Decision(
        point_id=point_id,
        stage=point.stage,
        mode=Mode.ACTIVE,
        value=value,
        confidence=confidence,
    )


def _messages(turns: int) -> list[dict]:
    out = [{"role": "system", "content": "你是助手"}]
    for index in range(turns):
        out.append({"role": "user", "content": f"问题 {index}：" + "细节" * 150})
        out.append({"role": "assistant", "content": f"回答 {index}：" + "说明" * 150})
    return out


def test_all_disabled_returns_equal_copy():
    messages = _messages(2)
    built = build_prompt(messages, [_decision("intent", "simple")], PromptConfig())
    assert built.messages == messages
    assert built.messages is not messages
    assert built.before_tokens == built.after_tokens
    assert built.prefill is None
    assert built.draft_prefix == ""
    assert built.memory_summary_tokens == 0


def test_memory_compresses_older_turns_and_keeps_recent():
    messages = _messages(6)
    config = PromptConfig(memory_enabled=True, keep_turns=2)
    built = build_prompt(messages, [_decision("intent", "simple")], config)
    assert memory_tokens(built.messages) < memory_tokens(messages)
    assert built.memory_summary_tokens > 0
    recent_users = [
        m for m in built.messages if m.get("role") == "user" and "问题 5" in m["content"]
    ]
    assert recent_users
    assert built.messages[0]["role"] == "system"
    assert "历史摘要" in built.messages[0]["content"]


def test_draft_prefix_prepended_without_dropping_original():
    messages = _messages(1)
    decisions = [_decision("intent", "simple", 0.9)]
    built = build_prompt(
        messages, decisions, PromptConfig(draft_enabled=True), registry=_REGISTRY
    )
    assert built.draft_prefix
    assert built.messages[1:] == messages
    assert built.messages[0] == {"role": "system", "content": built.draft_prefix}


def test_memory_runs_before_draft():
    messages = _messages(4)
    config = PromptConfig(memory_enabled=True, draft_enabled=True, keep_turns=1)
    built = build_prompt(
        messages, [_decision("intent", "simple")], config, registry=_REGISTRY
    )
    assert built.messages[0] == {"role": "system", "content": built.draft_prefix}
    assert any(
        m.get("role") == "system" and "历史摘要" in m.get("content", "")
        for m in built.messages[1:]
    )


def test_prefill_only_plans_when_prompt_long_enough():
    messages = _messages(1)
    empty = build_prompt(
        messages,
        [],
        PromptConfig(prefill_enabled=True),
        query="问题 0",
    )
    assert empty.prefill is not None
    assert empty.prefill.skipped == "short"
    assert empty.prefill.messages == messages

    long_messages = [
        {"role": "system", "content": "背景：" + "资料" * 2000},
        {"role": "user", "content": "请总结关键点"},
    ]
    planned = build_prompt(
        long_messages,
        [],
        PromptConfig(prefill_enabled=True),
        query="关键点",
    )
    assert planned.prefill is not None
    assert planned.prefill.skipped is None
    assert planned.prefill.after_tokens < planned.prefill.before_tokens


def test_invalid_keep_turns_rejected():
    with pytest.raises(ValueError):
        PromptConfig(keep_turns=0)
