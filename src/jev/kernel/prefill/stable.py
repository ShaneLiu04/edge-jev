"""AR009：文档级稳定压缩（CASC）。

与 AR006 的查询相关压缩不同，这里**不使用当前问题**选择文档块：
同一文档在任意问题下得到逐字一致的压缩前缀，因此可以与 AR007 的
prefix KV 复用叠加，而不破坏缓存命中。

算法：块级真实 embedding → 文档质心距离（稀有度）→ MMR 覆盖度 →
按预算选块 → 原文顺序重组。embedding 失败或无收益时安全回退原文。
"""

from __future__ import annotations

import dataclasses
import hashlib
import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence

from jev.errors import JevError, JevProtocolError
from jev.kernel.prefill.blocks import estimate_tokens, split_blocks
from jev.kernel.prefill.evidence import cosine

EmbedFn = Callable[[Sequence[str]], list[list[float]]]


@dataclasses.dataclass(frozen=True)
class StableConfig:
    enabled: bool = False
    ratio: float = 0.6
    block_tokens: int = 128
    min_tokens: int = 512
    min_blocks: int = 4
    mmr_lambda: float = 0.75
    cache_entries: int = 64
    embed_batch: int = 16

    def __post_init__(self) -> None:
        if not 0.0 < self.ratio < 1.0:
            raise ValueError(f"ratio 须在 (0,1): {self.ratio}")
        if self.block_tokens < 32:
            raise ValueError(f"block_tokens 须 >=32: {self.block_tokens}")
        if self.min_tokens < 0:
            raise ValueError(f"min_tokens 不得为负: {self.min_tokens}")
        if self.min_blocks < 2:
            raise ValueError(f"min_blocks 须 >=2: {self.min_blocks}")
        if not 0.0 <= self.mmr_lambda <= 1.0:
            raise ValueError(f"mmr_lambda 须在 [0,1]: {self.mmr_lambda}")
        if self.cache_entries < 0:
            raise ValueError(f"cache_entries 不得为负: {self.cache_entries}")
        if self.embed_batch < 1:
            raise ValueError(f"embed_batch 须 >=1: {self.embed_batch}")


@dataclasses.dataclass(frozen=True)
class StableResult:
    messages: list[dict]
    active: bool
    cache: str
    before_tokens: int
    after_tokens: int
    doc_tokens: int
    ratio: float
    ms: float
    skipped: str | None = None
    fallback: str | None = None


class StableCompressionCache:
    """文档哈希 → 压缩文本的线程安全 LRU。"""

    def __init__(self, max_entries: int = 64) -> None:
        if max_entries < 0:
            raise ValueError(f"max_entries 不得为负: {max_entries}")
        self._max = max_entries
        self._items: OrderedDict[str, tuple[str, int]] = OrderedDict()
        self._lock = threading.RLock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> tuple[str, int] | None:
        if self._max == 0:
            return None
        with self._lock:
            item = self._items.get(key)
            if item is None:
                self.misses += 1
                return None
            self._items.move_to_end(key)
            self.hits += 1
            return item

    def put(self, key: str, text: str, tokens: int) -> None:
        if self._max == 0:
            return
        with self._lock:
            self._items[key] = (text, tokens)
            self._items.move_to_end(key)
            while len(self._items) > self._max:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self.hits = 0
            self.misses = 0

    @property
    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"hits": self.hits, "misses": self.misses, "size": len(self._items)}


def split_document_query(content: str) -> tuple[str, str] | None:
    """把「长文档 + 末尾问题」拆成 (document, query)。

    仅在最后一段明显短于全文时视为问题；否则返回 None（不做压缩）。
    文档保留原始换行，避免改变逐字前缀。
    """
    text = str(content or "")
    if len(text) < 400:
        return None
    if "\n\n" not in text:
        return None
    document, query = text.rsplit("\n\n", 1)
    document = document.rstrip()
    query = query.strip()
    if not document or not query:
        return None
    if len(query) > max(200, int(len(text) * 0.25)):
        return None
    if estimate_tokens(document) < 512:
        return None
    return document, query


def rarity_scores(vectors: Sequence[Sequence[float]]) -> list[float]:
    """块向量到文档质心的距离；全部相同则返回 0.5。"""
    if not vectors:
        return []
    if len(vectors) == 1:
        return [1.0]
    dimension = len(vectors[0])
    if any(len(vector) != dimension for vector in vectors):
        raise JevProtocolError("块向量维度不一致")
    centroid = [
        sum(float(vector[index]) for vector in vectors) / len(vectors)
        for index in range(dimension)
    ]
    raw = [1.0 - cosine(vector, centroid) for vector in vectors]
    low = min(raw)
    high = max(raw)
    if math.isclose(low, high):
        return [0.5 for _ in vectors]
    return [(value - low) / (high - low) for value in raw]


def stable_select(
    tokens: Sequence[int],
    scores: Sequence[float],
    vectors: Sequence[Sequence[float]],
    *,
    ratio: float,
    mmr_lambda: float,
) -> set[int]:
    """首末块必选；其余按 rarity−λ·max_sim 贪心，预算内按原顺序输出。"""
    total = len(tokens)
    if total == 0:
        return set()
    if len(scores) != total:
        raise JevProtocolError("scores 与块数量不一致")
    budget = max(1, math.ceil(sum(tokens) * ratio))
    mandatory = {0, total - 1} if total > 1 else {0}
    selected = set(mandatory)
    used = sum(tokens[index] for index in selected)
    if used >= budget:
        return selected
    remaining = [index for index in range(total) if index not in selected]
    while remaining:
        best_index = -1
        best_utility = -math.inf
        for index in remaining:
            similarity = (
                max(cosine(vectors[index], vectors[j]) for j in selected)
                if vectors and selected
                else 0.0
            )
            utility = scores[index] - mmr_lambda * similarity
            if utility > best_utility or (
                math.isclose(utility, best_utility) and index < best_index
            ):
                best_utility = utility
                best_index = index
        if best_index < 0 or used + tokens[best_index] > budget:
            break
        selected.add(best_index)
        used += tokens[best_index]
        remaining.remove(best_index)
    return selected


def _doc_key(document: str, config: StableConfig) -> str:
    payload = (
        f"v1|r={config.ratio}|b={config.block_tokens}|m={config.mmr_lambda}|{document}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class StableCompressor:
    """查询无关的文档压缩器；同一文档的压缩结果可被多次请求复用。"""

    def __init__(
        self,
        embed: EmbedFn,
        config: StableConfig | None = None,
        cache: StableCompressionCache | None = None,
    ) -> None:
        self._embed = embed
        self.config = config or StableConfig()
        self.cache = cache or StableCompressionCache(self.config.cache_entries)

    def compress(self, messages: Sequence[dict]) -> StableResult:
        started = time.perf_counter()
        copied = [dict(message) for message in messages]
        before = sum(
            estimate_tokens(str(message.get("content", ""))) for message in copied
        )
        target = -1
        for index in range(len(copied) - 1, -1, -1):
            if copied[index].get("role") == "user":
                target = index
                break
        if target < 0:
            return self._inactive(copied, before, started, skipped="no_user")
        split = split_document_query(str(copied[target].get("content", "")))
        if split is None:
            return self._inactive(copied, before, started, skipped="no_document")
        document, query = split
        doc_tokens = estimate_tokens(document)
        key = _doc_key(document, self.config)
        cache_state = "off" if self.cache._max == 0 else "miss"
        cached = self.cache.get(key)
        if cached is not None:
            compressed, after_doc = cached
            cache_state = "hit"
        else:
            compressed, after_doc, fallback = self._compress_document(document)
            if fallback is not None:
                return self._inactive(
                    copied, before, started, skipped="fallback", fallback=fallback
                )
            self.cache.put(key, compressed, after_doc)
        copied[target] = {
            **copied[target],
            "content": f"{compressed}\n\n{query}",
        }
        after = sum(
            estimate_tokens(str(message.get("content", ""))) for message in copied
        )
        return StableResult(
            messages=copied,
            active=True,
            cache=cache_state,
            before_tokens=before,
            after_tokens=after,
            doc_tokens=doc_tokens,
            ratio=self.config.ratio,
            ms=round((time.perf_counter() - started) * 1000.0, 3),
        )

    def _compress_document(self, document: str) -> tuple[str, int, str | None]:
        blocks = split_blocks(
            [{"role": "user", "content": document}],
            block_tokens=self.config.block_tokens,
        )
        before = estimate_tokens(document)
        if before < self.config.min_tokens:
            return document, before, "short"
        if len(blocks) < self.config.min_blocks:
            return document, before, "few_blocks"
        try:
            texts = [block.text for block in blocks]
            vectors: list[list[float]] = []
            for start in range(0, len(texts), self.config.embed_batch):
                vectors.extend(
                    self._embed(texts[start : start + self.config.embed_batch])
                )
        except JevError as exc:
            return document, before, f"{type(exc).__name__}: {exc}"
        if len(vectors) != len(blocks):
            return document, before, "embedding_count_mismatch"
        if any(not vector for vector in vectors):
            return document, before, "embedding_empty"
        scores = rarity_scores(vectors)
        chosen = stable_select(
            [block.tokens for block in blocks],
            scores,
            vectors,
            ratio=self.config.ratio,
            mmr_lambda=self.config.mmr_lambda,
        )
        compressed = "\n\n".join(
            block.text for block in blocks if block.index in chosen
        )
        after = estimate_tokens(compressed)
        if not compressed or after >= before * 0.95:
            return document, before, "no_reduction"
        return compressed, after, None

    @staticmethod
    def _inactive(
        messages: list[dict],
        before: int,
        started: float,
        *,
        skipped: str,
        fallback: str | None = None,
    ) -> StableResult:
        return StableResult(
            messages=messages,
            active=False,
            cache="off",
            before_tokens=before,
            after_tokens=before,
            doc_tokens=0,
            ratio=0.0,
            ms=round((time.perf_counter() - started) * 1000.0, 3),
            skipped=skipped,
            fallback=fallback,
        )


__all__ = [
    "StableConfig",
    "StableCompressionCache",
    "StableCompressor",
    "StableResult",
    "rarity_scores",
    "split_document_query",
    "stable_select",
]
