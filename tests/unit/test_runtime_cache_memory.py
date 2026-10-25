"""AR003 T011/T012：语义缓存与长程记忆（纯逻辑，FR4-2/FR4-3）。"""

import pytest

from jev.kernel.cache.memory import compress_with_memory, memory_tokens
from jev.kernel.cache.semantic import (
    CacheConfig,
    SemanticCache,
    cosine,
    normalize_query,
)
from jev.kernel.decisions.builtin import load_builtin_registry
from jev.kernel.decisions.framework import Decision, Mode

_REGISTRY = load_builtin_registry()


class DictEmbeddings:
    """确定性向量表（纯逻辑桩，不是后端 mock）。"""

    def __init__(self, table: dict[str, list[float]], *, fail: bool = False):
        self.table = table
        self.fail = fail
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        if self.fail:
            raise RuntimeError("embedding down")
        return [self.table.get(text, [0.0, 0.0, 1.0]) for text in texts]


class TestCosine:
    def test_identical(self):
        assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)

    def test_orthogonal(self):
        assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_dimension_mismatch_and_zero(self):
        assert cosine([1.0], [1.0, 0.0]) == 0.0
        assert cosine([], []) == 0.0
        assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


class TestConfig:
    def test_invalid_values(self):
        for kwargs in (
            {"similarity": 0},
            {"similarity": 1.5},
            {"margin": -0.1},
            {"ttl_s": 0},
            {"capacity": 0},
        ):
            with pytest.raises(ValueError):
                CacheConfig(**kwargs)


class TestSemanticCache:
    def test_disabled_returns_none(self):
        cache = SemanticCache(CacheConfig(enabled=False))
        cache.store("f", "q", "a", "direct")
        assert cache.lookup("f", "q") is None

    def test_exact_hit(self):
        cache = SemanticCache(CacheConfig(enabled=True))
        cache.store("fp", "  Hello   World ", "答案", "light", now=100.0)
        hit = cache.lookup("fp", "hello world", now=101.0)
        assert hit is not None
        assert hit.layer == "exact"
        assert hit.answer == "答案"
        assert hit.age_s == pytest.approx(1.0)

    def test_normalization_equivalence(self):
        assert normalize_query("  A  B \n C ") == "a b c"

    def test_full_route_not_cached(self):
        cache = SemanticCache(CacheConfig(enabled=True))
        cache.store("fp", "q", "a", "full")
        assert cache.lookup("fp", "q") is None

    def test_fingerprint_isolation(self):
        cache = SemanticCache(CacheConfig(enabled=True))
        cache.store("fp-a", "q", "A 会话答案", "direct")
        assert cache.lookup("fp-b", "q") is None

    def test_semantic_hit_with_margin(self):
        embeddings = DictEmbeddings(
            {"问天气": [1.0, 0.0, 0.0], "天气如何": [0.99, 0.05, 0.0]}
        )
        cache = SemanticCache(CacheConfig(enabled=True, similarity=0.9, margin=0.02))
        cache.store("fp", "问天气", "晴天", "light", embeddings=embeddings)
        hit = cache.lookup("fp", "天气如何", embeddings=embeddings)
        assert hit is not None
        assert hit.layer == "semantic"
        assert hit.similarity > 0.9

    def test_semantic_margin_guard_blocks_close_candidates(self):
        embeddings = DictEmbeddings(
            {
                "问天气": [1.0, 0.0, 0.0],
                "问温度": [0.999, 0.01, 0.0],
                "天气如何": [0.995, 0.05, 0.0],
            }
        )
        cache = SemanticCache(CacheConfig(enabled=True, similarity=0.9, margin=0.2))
        cache.store("fp", "问天气", "A", "light", embeddings=embeddings)
        cache.store("fp", "问温度", "B", "light", embeddings=embeddings)
        assert cache.lookup("fp", "天气如何", embeddings=embeddings) is None

    def test_negative_examples_not_hit(self):
        embeddings = DictEmbeddings(
            {
                "北京是首都吗": [1.0, 0.0, 0.0],
                "北京不是首都吗": [0.2, 0.9, 0.0],
            }
        )
        cache = SemanticCache(CacheConfig(enabled=True, similarity=0.93, margin=0.02))
        cache.store("fp", "北京是首都吗", "是", "direct", embeddings=embeddings)
        assert cache.lookup("fp", "北京不是首都吗", embeddings=embeddings) is None

    def test_ttl_expiry(self):
        cache = SemanticCache(CacheConfig(enabled=True, ttl_s=10))
        cache.store("fp", "q", "a", "direct", now=100.0)
        assert cache.lookup("fp", "q", now=109.0) is not None
        assert cache.lookup("fp", "q", now=111.0) is None

    def test_lru_eviction(self):
        cache = SemanticCache(CacheConfig(enabled=True, capacity=2))
        cache.store("fp", "q1", "a1", "direct")
        cache.store("fp", "q2", "a2", "direct")
        cache.store("fp", "q3", "a3", "direct")
        assert cache.lookup("fp", "q1") is None
        assert cache.lookup("fp", "q3") is not None
        assert cache.stats()["evictions"] == 1

    def test_embedding_failure_degrades_to_exact(self):
        failing = DictEmbeddings({}, fail=True)
        cache = SemanticCache(CacheConfig(enabled=True))
        cache.store("fp", "q", "a", "direct", embeddings=failing)
        # 写入时向量失败 → 仍可精确命中
        assert cache.lookup("fp", "q").layer == "exact"
        # 查询时向量失败 → miss（不伪造）
        assert cache.lookup("fp", "other", embeddings=failing) is None

    def test_stats(self):
        cache = SemanticCache(CacheConfig(enabled=True))
        cache.store("fp", "q", "a", "direct")
        cache.lookup("fp", "q")
        cache.lookup("fp", "none")
        stats = cache.stats()
        assert stats["hits_exact"] == 1
        assert stats["misses"] == 1
        assert stats["stores"] == 1
        assert stats["size"] == 1

    def test_empty_answer_not_stored(self):
        cache = SemanticCache(CacheConfig(enabled=True))
        cache.store("fp", "q", "", "direct")
        assert cache.lookup("fp", "q") is None


def _decision(point_id: str, value: str) -> Decision:
    point = _REGISTRY.get(point_id)
    return Decision(
        point_id=point_id, stage=point.stage, mode=Mode.ACTIVE,
        value=value, confidence=0.9,
    )


def _history(turns: int = 5) -> list[dict]:
    messages: list[dict] = []
    for index in range(turns):
        messages.append({"role": "user", "content": f"第{index}轮问题：" + "细节" * 100})
        messages.append({"role": "assistant", "content": f"第{index}轮回答：" + "说明" * 100})
    return messages


class TestMemory:
    def test_keeps_recent_turns_verbatim(self):
        messages = _history(5)
        compressed = compress_with_memory(messages, keep_turns=2)
        assert compressed[0]["role"] == "system"
        assert compressed[-1]["content"] == messages[-1]["content"]
        assert len(compressed) == 1 + 4  # 摘要 + 最近 2 轮（4 条）

    def test_summary_length_bounded(self):
        compressed = compress_with_memory(_history(4), keep_turns=1)
        summary = compressed[0]["content"]
        assert len(summary) <= 2000
        for line in summary.splitlines()[1:]:
            # 每行 = role + 内容（≤120）+ 关键词
            assert len(line) <= 160

    def test_early_fact_retained_in_summary(self):
        messages = [
            {"role": "user", "content": "暗号是 BANANA"},
            {"role": "assistant", "content": "记住了"},
            {"role": "user", "content": "继续"},
            {"role": "assistant", "content": "好"},
            {"role": "user", "content": "再来"},
            {"role": "assistant", "content": "嗯"},
        ]
        compressed = compress_with_memory(messages, keep_turns=1)
        assert "BANANA" in compressed[0]["content"]

    def test_decisions_keywords_in_summary(self):
        compressed = compress_with_memory(
            _history(3), keep_turns=1, decisions=[_decision("intent", "complex")]
        )
        assert "intent=complex" in compressed[0]["content"]

    def test_token_saving(self):
        messages = _history(6)
        assert memory_tokens(compress_with_memory(messages, keep_turns=2)) < memory_tokens(messages)

    def test_invalid_keep_turns(self):
        with pytest.raises(ValueError):
            compress_with_memory(_history(2), keep_turns=0)

    def test_short_history_untouched(self):
        messages = _history(2)
        assert compress_with_memory(messages, keep_turns=2) == messages

    def test_deterministic(self):
        messages = _history(4)
        assert compress_with_memory(messages, keep_turns=1) == compress_with_memory(
            messages, keep_turns=1
        )
