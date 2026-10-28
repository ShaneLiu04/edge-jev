"""AR009：文档级稳定压缩（纯逻辑，确定性桩，不 mock 后端）。"""

from __future__ import annotations

import pytest

from jev.bench.matrix import load_matrix_config
from jev.errors import JevUnavailableError
from jev.kernel.prefill.stable import (
    StableCompressionCache,
    StableCompressor,
    StableConfig,
    rarity_scores,
    split_document_query,
    stable_select,
)


def _document(paragraphs: int = 40) -> str:
    lines = [
        f"第{index}段巡检记录：区域 {chr(65 + index % 6)} 的编号 K-{1000 + index} "
        f"温度为 {18 + index % 8}%湿度 {40 + index % 20}%，抽检 {10 + index} 件，"
        "记录已归档。"
        for index in range(paragraphs)
    ]
    return "\n\n".join(lines)


def _embed(texts):
    vectors = []
    for text in texts:
        vector = [0.0] * 8
        for position, char in enumerate(text):
            vector[position % 8] += (ord(char) % 97) / 97.0
        vectors.append(vector)
    return vectors


def test_split_document_query_zh_and_short():
    document = _document()
    content = f"以下是一份巡检记录。\n\n{document}\n\n问题：K-1003 的校验码是多少？只输出数字。"
    split = split_document_query(content)
    assert split is not None
    doc, query = split
    assert doc.endswith(document.split("\n\n")[-1])
    assert query.startswith("问题：K-1003")
    assert split_document_query("短文本") is None
    assert split_document_query("只有文档没有空行" * 100) is None


def test_rarity_prefers_outlier():
    scores = rarity_scores([[1.0, 0.0], [1.0, 0.0], [1.0, 0.0], [0.0, 1.0]])
    assert len(scores) == 4
    assert scores[3] > scores[0]


def test_stable_select_respects_budget_and_mandatory():
    chosen = stable_select(
        [10, 10, 10, 10],
        [0.1, 0.9, 0.8, 0.1],
        [[1.0, 0.0], [0.0, 1.0], [0.0, 1.0], [1.0, 0.0]],
        ratio=0.6,
        mmr_lambda=0.5,
    )
    assert chosen == {0, 3}


def test_compression_is_deterministic_and_prefix_stable():
    document = _document()
    config = StableConfig(enabled=True, ratio=0.6, block_tokens=128)
    compressor = StableCompressor(_embed, config)
    first = compressor.compress([{"role": "user", "content": f"{document}\n\n问题：K-1003？"}])
    second = StableCompressor(_embed, config).compress(
        [{"role": "user", "content": f"{document}\n\n问题：K-1021？"}]
    )
    assert first.active and second.active
    first_doc = first.messages[0]["content"].rsplit("\n\n", 1)[0]
    second_doc = second.messages[0]["content"].rsplit("\n\n", 1)[0]
    assert first_doc == second_doc
    assert first.after_tokens < first.before_tokens


def test_cache_hit_avoids_reembedding():
    calls = {"count": 0}

    def counting_embed(texts):
        calls["count"] += 1
        return _embed(texts)

    document = _document()
    config = StableConfig(enabled=True, ratio=0.6, block_tokens=128, cache_entries=8)
    compressor = StableCompressor(counting_embed, config)
    first = compressor.compress([{"role": "user", "content": f"{document}\n\n问题：A？"}])
    first_calls = calls["count"]
    second = compressor.compress([{"role": "user", "content": f"{document}\n\n问题：B？"}])
    assert first.cache == "miss"
    assert second.cache == "hit"
    assert first_calls > 0
    assert calls["count"] == first_calls
    assert compressor.cache.stats["hits"] == 1


def test_embedding_failure_falls_back_without_fake_vectors():
    def broken(_texts):
        raise JevUnavailableError("embedding down")

    document = _document()
    result = StableCompressor(
        broken, StableConfig(enabled=True, ratio=0.6, block_tokens=128)
    ).compress([{"role": "user", "content": f"{document}\n\n问题：A？"}])
    assert result.active is False
    assert result.fallback is not None
    assert result.messages[0]["content"].endswith("问题：A？")


def test_cache_lru_eviction():
    cache = StableCompressionCache(2)
    cache.put("a", "A", 1)
    cache.put("b", "B", 1)
    assert cache.get("a") is not None
    cache.put("c", "C", 1)
    assert cache.get("b") is None
    assert cache.get("a") is not None


def test_matrix_embedding_gpu_layers(tmp_path):
    config_file = tmp_path / "exp.toml"
    config_file.write_text(
        'name = "x"\narms = ["B1"]\ngpu_layers = 0\nembedding_gpu_layers = 99\n',
        encoding="utf-8",
    )
    config = load_matrix_config(config_file)
    assert config.gpu_layers == 0
    assert config.embedding_gpu_layers == 99


def test_invalid_stable_config():
    with pytest.raises(ValueError):
        StableConfig(ratio=1.0)
    with pytest.raises(ValueError):
        StableConfig(mmr_lambda=1.5)
