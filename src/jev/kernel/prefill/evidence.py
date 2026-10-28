"""证据驱动块打分（AR006 FR6-1/FR6-2，design §2.1）。

真实 embedding 余弦 + 词法 + 位置 + 决策信号融合；embedding 失败显式降级，
绝不伪造向量。MMR 用于抑制相邻冗余块挤占预算，锚点块强制保留。
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Callable, Mapping, Sequence

from jev.errors import JevError, JevProtocolError
from jev.kernel.prefill.blocks import Block
from jev.kernel.prefill.salience import (
    decision_signal,
    lexical_overlap,
    position_prior,
)


@dataclasses.dataclass(frozen=True)
class EvidenceConfig:
    embed_weight: float = 0.55
    lexical_weight: float = 0.25
    position_weight: float = 0.10
    decision_weight: float = 0.10
    mmr_lambda: float = 0.25
    min_ratio: float = 0.22
    max_ratio: float = 0.60
    embed_chunk_chars: int = 500
    embed_batch_chunks: int = 2

    def __post_init__(self) -> None:
        weights = (
            self.embed_weight
            + self.lexical_weight
            + self.position_weight
            + self.decision_weight
        )
        if not math.isclose(weights, 1.0, abs_tol=1e-6):
            raise ValueError(f"证据权重之和须为 1.0: {weights}")
        if not 0.0 <= self.mmr_lambda <= 1.0:
            raise ValueError(f"mmr_lambda 须在 [0,1]: {self.mmr_lambda}")
        if not 0.0 < self.min_ratio <= self.max_ratio <= 1.0:
            raise ValueError(
                f"预算比例须满足 0<min<=max<=1: {self.min_ratio},{self.max_ratio}"
            )
        if self.embed_chunk_chars < 32:
            raise ValueError(f"embed_chunk_chars 须 >=32: {self.embed_chunk_chars}")
        if self.embed_batch_chunks < 1:
            raise ValueError(f"embed_batch_chunks 须 >=1: {self.embed_batch_chunks}")


@dataclasses.dataclass(frozen=True)
class EvidenceResult:
    scores: tuple[float, ...] = ()
    vectors: tuple[tuple[float, ...], ...] = ()
    fallback: str | None = None
    entropy: float = 0.0
    top_gap: float = 0.0
    margin: float = 0.0

    @property
    def usable(self) -> bool:
        return self.fallback is None and bool(self.scores)


def cosine(first: Sequence[float], second: Sequence[float]) -> float:
    if not first or not second or len(first) != len(second):
        return 0.0
    dot = sum(a * b for a, b in zip(first, second, strict=True))
    left = sum(a * a for a in first) ** 0.5
    right = sum(b * b for b in second) ** 0.5
    if left == 0.0 or right == 0.0:
        return 0.0
    return dot / (left * right)


def retrieval_query(text: str) -> str:
    """从「长文 + 末尾问题」式输入中抽取真正的检索意图。

    长上下文请求常把文档与问题放在同一条 user 消息里；直接对全文取 embedding
    会被文档主体稀释。这里优先取末尾问题段，失败则回退全文。
    """
    stripped = str(text or "").strip()
    if len(stripped) < 200:
        return stripped
    paragraphs = [part.strip() for part in stripped.split("\n\n") if part.strip()]
    if len(paragraphs) >= 2:
        last = paragraphs[-1]
        if len(last) <= max(200, int(len(stripped) * 0.2)):
            return last
    return stripped


def _minmax(values: Sequence[float]) -> list[float]:
    if not values:
        return []
    low = min(values)
    high = max(values)
    if math.isclose(low, high):
        return [0.5 for _ in values]
    span = high - low
    return [(value - low) / span for value in values]


def _softmax_entropy(scores: Sequence[float]) -> float:
    if len(scores) <= 1:
        return 0.0
    positive = [max(0.0, float(score)) for score in scores]
    total = sum(positive)
    if total <= 0.0:
        return 1.0
    probabilities = [value / total for value in positive]
    entropy = -sum(p * math.log(p) for p in probabilities if p > 0.0)
    return max(0.0, min(1.0, entropy / math.log(len(probabilities))))


def _top_gap(scores: Sequence[float]) -> float:
    if len(scores) < 2:
        return 1.0
    ordered = sorted((max(0.0, float(score)) for score in scores), reverse=True)
    return max(0.0, min(1.0, (ordered[0] - ordered[1]) / (ordered[0] + 1e-9)))


def mmr_order(
    scores: Sequence[float],
    vectors: Sequence[Sequence[float]] | None,
    *,
    mmr_lambda: float,
    already: Sequence[int] = (),
) -> list[int]:
    """确定性 MMR 排序：utility = score - λ·max_sim(已选)。

    无向量时退化为按分数降序（并列按索引），保证与原逻辑兼容。
    """
    count = len(scores)
    remaining = [index for index in range(count) if index not in set(already)]
    chosen: list[int] = []
    if vectors is None:
        return sorted(range(count), key=lambda index: (-scores[index], index))
    while remaining:
        best_index = remaining[0]
        best_value = -math.inf
        for index in remaining:
            redundancy = 0.0
            for other in already:
                redundancy = max(redundancy, cosine(vectors[index], vectors[other]))
            for other in chosen:
                redundancy = max(redundancy, cosine(vectors[index], vectors[other]))
            utility = scores[index] - mmr_lambda * redundancy
            if utility > best_value + 1e-12 or (
                math.isclose(utility, best_value) and index < best_index
            ):
                best_value = utility
                best_index = index
        chosen.append(best_index)
        remaining.remove(best_index)
    return chosen


def _relative_margin(scores: Sequence[float]) -> float:
    """最高分相对均值的相对优势；证据集中时接近 1，分布均匀时接近 0。"""
    if not scores:
        return 0.0
    positive = [max(0.0, float(score)) for score in scores]
    top = max(positive)
    if top <= 0.0:
        return 0.0
    mean = sum(positive) / len(positive)
    return max(0.0, min(1.0, (top - mean) / (top + 1e-9)))


def adaptive_ratio(result: EvidenceResult, config: EvidenceConfig) -> float:
    """证据越集中（top-gap/相对均值越大）越激进；分布均匀时用上界。"""
    if not result.usable:
        return config.max_ratio
    concentration = 0.5 * result.top_gap + 0.5 * result.margin
    concentration = max(0.0, min(1.0, concentration))
    return config.min_ratio + (config.max_ratio - config.min_ratio) * (
        1.0 - concentration
    )


class EvidenceSelector:
    """真实 embedding 打分器；embed 必须为真实调用的绑定方法。"""

    def __init__(
        self,
        embed: Callable[[list[str]], list[list[float]]],
        config: EvidenceConfig | None = None,
    ) -> None:
        self._embed = embed
        self._config = config if config is not None else EvidenceConfig()

    @property
    def config(self) -> EvidenceConfig:
        return self._config

    def _chunks(self, text: str) -> list[str]:
        size = self._config.embed_chunk_chars
        return [text[index:index + size] for index in range(0, len(text), size)] or [""]

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """公开的真实 embedding 入口（AR009 文档级压缩复用）。"""
        return self._embed_texts(texts)

    def _embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """长文本按字符切块后分批真实 embedding，再按 owner 均值聚合。"""
        owners: list[int] = []
        chunks: list[str] = []
        for owner, text in enumerate(texts):
            for chunk in self._chunks(str(text)):
                owners.append(owner)
                chunks.append(chunk)
        gathered: list[list[float] | None] = [None] * len(chunks)
        batch = self._config.embed_batch_chunks
        for start in range(0, len(chunks), batch):
            part = chunks[start:start + batch]
            vectors = self._embed(part)
            if len(vectors) != len(part):
                raise JevProtocolError(
                    f"embedding 条目数不匹配: {len(vectors)} != {len(part)}"
                )
            for offset, vector in enumerate(vectors):
                gathered[start + offset] = [float(value) for value in vector]
        grouped: dict[int, list[list[float]]] = {}
        for owner, vector in zip(owners, gathered, strict=True):
            if vector is None:
                raise JevProtocolError("embedding 分块缺失")
            grouped.setdefault(owner, []).append(vector)
        aggregated: list[list[float]] = []
        dimension: int | None = None
        for owner in range(len(texts)):
            group = grouped.get(owner)
            if not group:
                raise JevProtocolError(f"embedding owner {owner} 无向量")
            size = len(group[0])
            if dimension is None:
                dimension = size
            elif size != dimension:
                raise JevProtocolError(f"embedding 维度不一致: {size} != {dimension}")
            if any(len(vector) != size for vector in group):
                raise JevProtocolError("embedding 分块维度不一致")
            aggregated.append(
                [
                    sum(vector[index] for vector in group) / len(group)
                    for index in range(size)
                ]
            )
        return aggregated

    def score(
        self,
        blocks: Sequence[Block],
        query: str,
        applied: Mapping[str, str | float | None],
    ) -> EvidenceResult:
        if not blocks:
            return EvidenceResult()
        texts = [str(query), *[block.text for block in blocks]]
        retrieval = retrieval_query(str(query))
        embed_texts = [retrieval, *[block.text for block in blocks]]
        try:
            embeddings = self._embed_texts(embed_texts)
        except JevError as exc:
            return EvidenceResult(fallback=f"{type(exc).__name__}: {exc}")
        if len(embeddings) != len(texts):
            return EvidenceResult(
                fallback=f"embedding 条目数不匹配: {len(embeddings)} != {len(texts)}"
            )
        query_vector = embeddings[0]
        vectors = [tuple(float(value) for value in vector) for vector in embeddings[1:]]
        raw = [cosine(query_vector, vector) for vector in vectors]
        normalized = _minmax(raw)
        total = len(blocks)
        scores = [
            self._config.embed_weight * normalized[index]
            + self._config.lexical_weight * lexical_overlap(block.text, query)
            + self._config.position_weight * position_prior(block.index, total)
            + self._config.decision_weight * decision_signal(block.text, applied)
            for index, block in enumerate(blocks)
        ]
        return EvidenceResult(
            scores=tuple(scores),
            vectors=tuple(vectors),
            entropy=_softmax_entropy(normalized),
            top_gap=_top_gap(scores),
            margin=_relative_margin(scores),
        )


__all__ = [
    "EvidenceConfig",
    "EvidenceResult",
    "EvidenceSelector",
    "adaptive_ratio",
    "cosine",
    "mmr_order",
]
