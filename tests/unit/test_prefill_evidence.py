"""AR006 FR6-1/FR6-2：证据选择、MMR、自适应预算（纯逻辑，无后端）。"""

import math

import pytest

from jev.errors import JevUnavailableError
from jev.kernel.prefill.blocks import Block
from jev.kernel.prefill.evidence import (
    EvidenceConfig,
    EvidenceResult,
    EvidenceSelector,
    adaptive_ratio,
    cosine,
    mmr_order,
    retrieval_query,
)
from jev.kernel.prefill.planner import PrefillConfig, plan_prefill


def _embed(text: str) -> list[float]:
    vector = [0.0] * 64
    lowered = text.lower()
    for index in range(len(lowered) - 1):
        bigram = lowered[index:index + 2]
        vector[sum(ord(char) for char in bigram) % 64] += 1.0
    return vector


def _embed_texts(texts: list[str]) -> list[list[float]]:
    return [_embed(text) for text in texts]


def _block(index: int, text: str, tokens: int = 100) -> Block:
    return Block(index=index, message_index=0, role="user", text=text, tokens=tokens)


def test_cosine_and_weights_validation():
    assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine([], [1.0]) == 0.0
    with pytest.raises(ValueError):
        EvidenceConfig(embed_weight=0.9)
    with pytest.raises(ValueError):
        EvidenceConfig(min_ratio=0.8, max_ratio=0.5)


def test_evidence_prefers_target_block_and_reports_entropy():
    blocks = [
        _block(0, "开头说明 " + "背景" * 40),
        _block(1, "无关段落 " + "天气" * 40),
        _block(2, "特别记录 alpha alpha alpha 验证码 4815 " + "alpha " * 20),
        _block(3, "结尾 " + "总结" * 40),
    ]
    selector = EvidenceSelector(_embed_texts)
    result = selector.score(blocks, "alpha 的验证码是多少", {})
    assert result.usable
    assert len(result.scores) == 4
    assert max(range(4), key=lambda index: result.scores[index]) == 2
    assert 0.0 <= result.entropy <= 1.0
    assert 0.0 <= result.top_gap <= 1.0


def test_embedding_failure_degrades_without_fake_vectors():
    def boom(_texts):
        raise JevUnavailableError("embedding down")

    selector = EvidenceSelector(boom)
    result = selector.score([_block(0, "x"), _block(1, "y")], "x", {})
    assert not result.usable
    assert result.scores == ()
    assert "JevUnavailableError" in (result.fallback or "")


def test_mmr_suppresses_redundant_blocks():
    vectors = [(1.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
    order = mmr_order([0.9, 0.9, 0.8], vectors, mmr_lambda=0.5)
    assert order[0] == 0
    assert order[1] == 2
    order_no_vectors = mmr_order([0.9, 0.9, 0.8], None, mmr_lambda=0.5)
    assert order_no_vectors == [0, 1, 2]


def test_adaptive_ratio_monotonic_between_bounds():
    config = EvidenceConfig(min_ratio=0.22, max_ratio=0.60)
    concentrated = EvidenceResult(
        scores=(0.9, 0.1), entropy=0.0, top_gap=0.9, margin=0.9
    )
    diffuse = EvidenceResult(
        scores=(0.5, 0.5), entropy=1.0, top_gap=0.0, margin=0.0
    )
    low = adaptive_ratio(concentrated, config)
    high = adaptive_ratio(diffuse, config)
    assert 0.22 <= low < high <= 0.60
    assert adaptive_ratio(EvidenceResult(), config) == 0.60


def _long_messages() -> list[dict]:
    paragraphs = []
    for index in range(9):
        filler = "无关内容" * 90 if index != 5 else "alpha 记录 4815 " + "alpha " * 60
        paragraphs.append(filler)
    body = "\n\n".join(paragraphs)
    return [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": body},
        {"role": "user", "content": "alpha 的验证码是多少"},
    ]


def test_plan_prefill_keeps_anchor_and_adaptive_budget():
    selector = EvidenceSelector(_embed_texts)
    config = PrefillConfig(
        enabled=True,
        evidence=True,
        adaptive=True,
        anchor=True,
        budget_ratio=0.6,
        min_blocks=4,
        block_tokens=64,
    )
    plan = plan_prefill(
        _long_messages(), "alpha 的验证码", {}, config, evidence=selector
    )
    assert plan.selector == "evidence"
    assert plan.skipped is None
    assert 0.22 <= plan.budget_ratio <= 0.60
    joined = "\n".join(message["content"] for message in plan.messages)
    assert "4815" in joined
    assert plan.after_tokens < plan.before_tokens


def test_plan_prefill_falls_back_to_salience():
    def boom(_texts):
        raise JevUnavailableError("down")

    config = PrefillConfig(
        enabled=True,
        evidence=True,
        adaptive=True,
        min_blocks=4,
        block_tokens=64,
    )
    fallback_plan = plan_prefill(
        _long_messages(), "alpha 的验证码", {}, config, evidence=EvidenceSelector(boom)
    )
    salience_plan = plan_prefill(
        _long_messages(),
        "alpha 的验证码",
        {},
        PrefillConfig(enabled=True, min_blocks=4, block_tokens=64),
    )
    assert fallback_plan.selector == "salience"
    assert fallback_plan.fallback
    assert fallback_plan.messages == salience_plan.messages
    assert fallback_plan.budget_ratio == config.budget_ratio


def test_plan_prefill_without_evidence_is_unchanged():
    messages = _long_messages()
    config = PrefillConfig(enabled=True, min_blocks=4, block_tokens=64)
    plan = plan_prefill(messages, "验证码", {}, config)
    assert plan.selector == "salience"
    assert plan.fallback is None
    assert math.isclose(plan.budget_ratio, 0.6)


def test_prompt_allocator_uses_evidence_and_adaptive_ratio():
    from jev.kernel.runtime.prompt import PromptConfig, build_prompt

    selector = EvidenceSelector(_embed_texts)
    config = PromptConfig(
        allocator_enabled=True,
        allocator_budget_ratio=0.6,
        allocator_min_prompt_tokens=256,
        evidence_enabled=True,
        adaptive=True,
    )
    built = build_prompt(
        _long_messages(),
        [],
        config,
        query="alpha 的验证码",
        prefill_config=PrefillConfig(enabled=True, min_blocks=4, block_tokens=64),
        evidence=selector,
    )
    assert built.allocation is not None
    assert built.allocation.selector == "evidence"
    assert 0.22 <= built.allocation.budget_ratio <= 0.60
    assert built.allocation.after_tokens < built.allocation.before_tokens
    joined = "\n".join(message["content"] for message in built.messages)
    assert "4815" in joined


def test_prompt_allocator_disabled_evidence_is_unchanged():
    from jev.kernel.runtime.prompt import PromptConfig, build_prompt

    built = build_prompt(
        _long_messages(),
        [],
        PromptConfig(allocator_enabled=True),
        query="alpha 的验证码",
        prefill_config=PrefillConfig(enabled=True, min_blocks=4, block_tokens=64),
    )
    assert built.allocation is not None
    assert built.allocation.selector == "salience"
    assert built.allocation.budget_ratio == 0.6


def test_long_blocks_are_chunked_before_embedding():
    calls: list[list[str]] = []

    def recording_embed(texts: list[str]) -> list[list[float]]:
        calls.append(list(texts))
        return [_embed(text) for text in texts]

    config = EvidenceConfig(embed_chunk_chars=32, embed_batch_chunks=2)
    selector = EvidenceSelector(recording_embed, config)
    blocks = [
        _block(0, "alpha " * 100),
        _block(1, "beta " * 100),
    ]
    result = selector.score(blocks, "alpha", {})
    assert result.usable
    assert all(len(text) <= 32 for batch in calls for text in batch)
    assert all(len(batch) <= 2 for batch in calls)
    assert len(result.vectors) == 2


def test_retrieval_query_extracts_tail_question():
    document = "巡检记录：" + "无关内容。" * 100
    text = f"{document}\n\n问题：编号 K-1000 的校验码是多少？只输出数字。"
    assert retrieval_query(text).startswith("问题：")
    assert retrieval_query("短问题") == "短问题"


def test_full_document_query_still_anchors_target():
    document = "\n\n".join(
        [
            "开头 " + "背景" * 40,
            "普通记录 1 " + "巡检" * 40,
            "特别记录 alpha 校验码 4815 " + "alpha " * 30,
            "普通记录 2 " + "归档" * 40,
            "结尾 " + "总结" * 40,
        ]
    )
    prompt = f"{document}\n\n问题：alpha 的校验码是多少？只输出数字。"
    messages = [{"role": "user", "content": prompt}]
    selector = EvidenceSelector(_embed_texts)
    config = PrefillConfig(
        enabled=True,
        evidence=True,
        adaptive=True,
        anchor=True,
        min_blocks=4,
        min_prompt_tokens=50,
        block_tokens=64,
    )
    plan = plan_prefill(messages, prompt, {}, config, evidence=selector)
    joined = "\n".join(message["content"] for message in plan.messages)
    assert "4815" in joined
    assert plan.selector == "evidence"
