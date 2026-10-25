"""AR003 T008：稀疏 prefill 规划器（FR3-1/FR3-2，纯逻辑）。"""

import pytest

from jev.kernel.prefill.planner import (
    PrefillConfig,
    PrefillPlan,
    plan_prefill,
    split_blocks,
)
from jev.kernel.prefill.salience import lexical_overlap, score_blocks


def _long_messages(paragraphs: int = 10, chars: int = 200) -> list[dict]:
    content = "\n\n".join(f"第{i}段：" + "内容" * (chars // 2) for i in range(paragraphs))
    return [{"role": "system", "content": "系统提示"}, {"role": "user", "content": content}]


class TestSplitBlocks:
    def test_splits_paragraphs(self):
        blocks = split_blocks([{"role": "user", "content": "一\n\n二\n\n三"}], block_tokens=256)
        assert [block.text for block in blocks] == ["一", "二", "三"]
        assert all(block.role == "user" for block in blocks)

    def test_splits_overlong_paragraph(self):
        long_text = "句子。" * 500
        blocks = split_blocks([{"role": "user", "content": long_text}], block_tokens=32)
        assert len(blocks) > 1
        assert all(block.tokens <= 32 + 5 for block in blocks)

    def test_drops_empty(self):
        blocks = split_blocks([{"role": "user", "content": "a\n\n\n\nb"}], block_tokens=256)
        assert len(blocks) == 2

    def test_preserves_order_and_index(self):
        blocks = split_blocks([{"role": "user", "content": "a\n\nb\n\nc"}], block_tokens=256)
        assert [block.index for block in blocks] == [0, 1, 2]

    def test_invalid_block_tokens(self):
        with pytest.raises(ValueError):
            split_blocks([{"role": "user", "content": "x"}], block_tokens=0)


class TestSalience:
    def test_lexical_overlap_english(self):
        # "api" 交集；"tool"/"calling"/"call"/"how"/"to"/"the" 并集
        assert lexical_overlap("tool calling api", "how to call the api") == pytest.approx(1 / 7)
        assert lexical_overlap("python sort", "python") == pytest.approx(1 / 2)
        assert lexical_overlap("abc", "xyz") == 0.0

    def test_lexical_overlap_chinese_bigrams(self):
        assert lexical_overlap("函数式编程", "函数式") > 0
        assert lexical_overlap("完全无关", "函数式") == 0

    def test_scores_deterministic(self):
        messages = _long_messages(6)
        blocks = split_blocks(messages, block_tokens=256)
        first = score_blocks(blocks, "内容", {})
        second = score_blocks(blocks, "内容", {})
        assert first == second

    def test_decision_signal_boosts_matching_blocks(self):
        messages = [
            {"role": "user", "content": "请调用 tool 查询天气\n\n普通闲聊内容"},
        ]
        blocks = split_blocks(messages, block_tokens=256)
        scores = score_blocks(blocks, "天气", {"needs_tool": "tool"})
        assert scores[0] > scores[1]

    def test_query_boost(self):
        messages = [{"role": "user", "content": "关于排序算法的说明\n\n无关段落"}]
        blocks = split_blocks(messages, block_tokens=256)
        scores = score_blocks(blocks, "排序算法", {})
        assert scores[0] > scores[1]


class TestPlanPrefill:
    def test_short_prompt_skipped(self):
        plan = plan_prefill(
            [{"role": "user", "content": "短问题"}],
            "短问题",
            {},
            PrefillConfig(enabled=True),
        )
        assert isinstance(plan, PrefillPlan)
        assert plan.skipped == "short"
        assert plan.messages == [{"role": "user", "content": "短问题"}]

    def test_few_blocks_skipped(self):
        content = "\n\n".join("长" * 900 for _ in range(3))
        messages = [{"role": "user", "content": content}]
        plan = plan_prefill(
            messages, "问题", {}, PrefillConfig(enabled=True, budget_ratio=0.9)
        )
        assert plan.total == 3
        assert plan.skipped == "few_blocks"

    def test_reduces_tokens_and_keeps_order(self):
        messages = _long_messages(12)
        plan = plan_prefill(messages, "内容", {}, PrefillConfig(enabled=True, budget_ratio=0.5))
        assert plan.skipped is None
        assert plan.after_tokens < plan.before_tokens
        assert plan.selected < plan.total
        # 首块（system）与末块（当前 user）必选
        assert plan.messages[0]["role"] == "system"
        assert plan.messages[0]["content"] == "系统提示"
        assert plan.messages[-1]["content"].endswith(plan.messages[-1]["content"][-10:])

    def test_mandatory_blocks_over_budget_skips(self):
        small = [
            {"role": "system", "content": "系统" * 2000},
            {"role": "user", "content": "问题"},
        ]
        plan = plan_prefill(
            small,
            "问题",
            {},
            PrefillConfig(enabled=True, budget_ratio=0.1, min_prompt_tokens=0),
        )
        assert plan.skipped in ("mandatory_over_budget", "few_blocks")

    def test_budget_ratio_validated(self):
        for bad in (0, -0.1, 1.5):
            with pytest.raises(ValueError):
                PrefillConfig(budget_ratio=bad)

    def test_enabled_flag_short_circuits(self):
        messages = _long_messages(8)
        plan = plan_prefill(messages, "内容", {}, PrefillConfig(enabled=False))
        assert plan.skipped == "disabled"
        assert plan.messages == messages

    def test_deterministic(self):
        messages = _long_messages(10)
        config = PrefillConfig(enabled=True, budget_ratio=0.5)
        first = plan_prefill(messages, "内容", {"format": "text"}, config)
        second = plan_prefill(messages, "内容", {"format": "text"}, config)
        assert first == second

    def test_plan_tokens_are_estimates(self):
        plan = plan_prefill(
            _long_messages(8), "内容", {}, PrefillConfig(enabled=True, budget_ratio=0.6)
        )
        assert plan.before_tokens > 0
        assert 0 < plan.after_tokens <= plan.before_tokens
