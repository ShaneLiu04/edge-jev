"""语义缓存（AR003 FR4-2，design §4.2.2(8)）。

两级：精确键（规范化消息 + 前缀指纹）+ 语义近似（真实 embedding 余弦）。
只缓存 direct/light 答案；语义命中要求「相似度阈值 + 与次优的边际」双约束，
降低否定/数字变更等近似误命中。LRU + TTL；embedding 不可用 → 仅精确层。
"""

from __future__ import annotations

import dataclasses
import hashlib
import math
import re
import time
from collections import OrderedDict
from collections.abc import Sequence
from typing import Protocol

from jev.kernel.runtime.conformal import AdmissionPolicy

_CACHEABLE_ROUTES = frozenset({"direct", "light"})
_WHITESPACE = re.compile(r"\s+")


class EmbeddingProvider(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


@dataclasses.dataclass(frozen=True)
class CacheConfig:
    enabled: bool = False
    similarity: float = 0.93
    margin: float = 0.02
    ttl_s: float = 1800.0
    capacity: int = 256

    def __post_init__(self) -> None:
        if not 0.0 < self.similarity <= 1.0:
            raise ValueError(f"similarity 须在 (0,1]: {self.similarity}")
        if not 0.0 <= self.margin <= 1.0:
            raise ValueError(f"margin 须在 [0,1]: {self.margin}")
        if self.ttl_s <= 0:
            raise ValueError(f"ttl_s 须 > 0: {self.ttl_s}")
        if self.capacity < 1:
            raise ValueError(f"capacity 须 ≥1: {self.capacity}")


@dataclasses.dataclass(frozen=True)
class CacheHit:
    layer: str  # exact | semantic
    similarity: float | None
    answer: str
    route: str
    age_s: float
    admission: str = "similarity"
    threshold: float | None = None


@dataclasses.dataclass
class _Entry:
    fingerprint: str
    query: str
    answer: str
    route: str
    created_at: float
    vector: list[float] | None = None


def normalize_query(text: str) -> str:
    return _WHITESPACE.sub(" ", text.strip().lower())


def _exact_key(fingerprint: str, query: str) -> str:
    payload = f"{fingerprint}\x1f{normalize_query(query)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cosine(first: Sequence[float], second: Sequence[float]) -> float:
    if len(first) != len(second) or not first:
        return 0.0
    dot = sum(a * b for a, b in zip(first, second, strict=True))
    norm_a = math.sqrt(sum(a * a for a in first))
    norm_b = math.sqrt(sum(b * b for b in second))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class SemanticCache:
    """进程内缓存（单实例；线程安全由调用方串行化或由 server 事件循环保证）。"""

    def __init__(self, config: CacheConfig) -> None:
        self._config = config
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._hits_exact = 0
        self._hits_semantic = 0
        self._misses = 0
        self._stores = 0
        self._evictions = 0

    @property
    def config(self) -> CacheConfig:
        return self._config

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def lookup(
        self,
        fingerprint: str,
        query: str,
        *,
        embeddings: EmbeddingProvider | None = None,
        now: float | None = None,
        admission: AdmissionPolicy | None = None,
    ) -> CacheHit | None:
        if not self._config.enabled:
            return None
        moment = time.time() if now is None else now
        self._purge_expired(moment)
        key = _exact_key(fingerprint, query)
        entry = self._entries.get(key)
        if entry is not None:
            self._entries.move_to_end(key)
            self._hits_exact += 1
            return CacheHit(
                layer="exact",
                similarity=1.0,
                answer=entry.answer,
                route=entry.route,
                age_s=moment - entry.created_at,
            )

        best, second, best_key = 0.0, 0.0, None
        if embeddings is not None:
            try:
                vector = embeddings.embed([query])[0]
            except Exception:  # noqa: BLE001 - 失败安全：本次降级精确层
                self._misses += 1
                return None
            for candidate_key, candidate in self._entries.items():
                if candidate.vector is None:
                    continue
                score = cosine(vector, candidate.vector)
                if score > best:
                    second = best
                    best = score
                    best_key = candidate_key
                elif score > second:
                    second = score
            if (
                best_key is not None
                and (
                    admission.allows(best, second)
                    if admission is not None and admission.enabled
                    else best >= self._config.similarity
                    and (best - second) >= self._config.margin
                )
            ):
                candidate = self._entries[best_key]
                self._entries.move_to_end(best_key)
                self._hits_semantic += 1
                return CacheHit(
                    layer="semantic",
                    similarity=round(best, 6),
                    answer=candidate.answer,
                    route=candidate.route,
                    age_s=moment - candidate.created_at,
                    admission=(
                        "conformal"
                        if admission is not None and admission.enabled
                        else "similarity"
                    ),
                    threshold=(
                        admission.threshold
                        if admission is not None and admission.enabled
                        else self._config.similarity
                    ),
                )
        self._misses += 1
        return None

    def store(
        self,
        fingerprint: str,
        query: str,
        answer: str,
        route: str,
        *,
        embeddings: EmbeddingProvider | None = None,
        now: float | None = None,
    ) -> None:
        if not self._config.enabled or route not in _CACHEABLE_ROUTES or not answer:
            return
        moment = time.time() if now is None else now
        key = _exact_key(fingerprint, query)
        vector: list[float] | None = None
        if embeddings is not None:
            try:
                vector = embeddings.embed([query])[0]
            except Exception:  # noqa: BLE001 - 语义层可选，失败仅存精确层
                vector = None
        self._entries[key] = _Entry(
            fingerprint=fingerprint,
            query=normalize_query(query),
            answer=answer,
            route=route,
            created_at=moment,
            vector=vector,
        )
        self._entries.move_to_end(key)
        self._stores += 1
        while len(self._entries) > self._config.capacity:
            self._entries.popitem(last=False)
            self._evictions += 1

    def purge(self, *, now: float | None = None) -> int:
        return self._purge_expired(time.time() if now is None else now)

    def stats(self) -> dict:
        return {
            "size": len(self._entries),
            "hits_exact": self._hits_exact,
            "hits_semantic": self._hits_semantic,
            "misses": self._misses,
            "stores": self._stores,
            "evictions": self._evictions,
        }

    def _purge_expired(self, moment: float) -> int:
        expired = [
            key
            for key, entry in self._entries.items()
            if moment - entry.created_at > self._config.ttl_s
        ]
        for key in expired:
            del self._entries[key]
        return len(expired)


__all__ = [
    "CacheConfig",
    "CacheHit",
    "EmbeddingProvider",
    "SemanticCache",
    "cosine",
    "normalize_query",
]
