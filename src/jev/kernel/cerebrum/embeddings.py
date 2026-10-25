"""真实 embedding 客户端（AR003 FR4-1，design §4.3.1）。

独立 llama-server 实例（`--embeddings --pooling mean`）提供向量；
不可用即降级（调用方禁用缓存），绝不伪造向量。
"""

from __future__ import annotations

import httpx

from jev.errors import JevProtocolError, JevTimeoutError, JevUnavailableError
from jev.kernel.cerebrum.llama_server import LlamaServerManager

_MAX_TEXTS = 64


class EmbeddingClient:
    """OpenAI 兼容 `/v1/embeddings` 客户端（仅真实 HTTP）。"""

    def __init__(
        self,
        base_url: str,
        *,
        model: str = "local",
        timeout_s: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout_s = float(timeout_s)
        self._client = httpx.Client(timeout=self._timeout_s)

    @property
    def base_url(self) -> str:
        return self._base_url

    def available(self) -> bool:
        """探测一次真实调用；任何失败 → False（不抛，供启动降级）。"""
        try:
            self.embed(["ping"])
        except (JevUnavailableError, JevProtocolError):
            return False
        return True

    def embed(self, texts: list[str] | tuple[str, ...]) -> list[list[float]]:
        items = [str(text) for text in texts]
        if not items:
            return []
        if len(items) > _MAX_TEXTS:
            raise ValueError(f"单次 embedding 文本数不得超过 {_MAX_TEXTS}: {len(items)}")
        payload = {"model": self._model, "input": items}
        try:
            response = self._client.post(f"{self._base_url}/v1/embeddings", json=payload)
        except httpx.TimeoutException as exc:
            raise JevTimeoutError(f"embedding 服务超时（{self._base_url}）: {exc}") from exc
        except httpx.HTTPError as exc:
            raise JevUnavailableError(
                f"embedding 服务不可达（{self._base_url}）: {exc}"
            ) from exc
        if not 200 <= response.status_code < 300:
            raise JevProtocolError(
                f"embedding 服务错误（{self._base_url}）: "
                f"HTTP {response.status_code} {response.text[:200]}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise JevProtocolError(
                f"embedding 响应不是合法 JSON: {response.text[:200]}"
            ) from exc
        entries = data.get("data") if isinstance(data, dict) else None
        if not isinstance(entries, list) or len(entries) != len(items):
            raise JevProtocolError(
                f"embedding 响应条目数不匹配: 期望 {len(items)}，实际 "
                f"{len(entries) if isinstance(entries, list) else 'N/A'}"
            )
        vectors: list[list[float]] = []
        dimension: int | None = None
        for entry in entries:
            vector = entry.get("embedding") if isinstance(entry, dict) else None
            if not isinstance(vector, list) or not vector:
                raise JevProtocolError("embedding 条目缺少 embedding 向量")
            try:
                values = [float(value) for value in vector]
            except (TypeError, ValueError) as exc:
                raise JevProtocolError("embedding 向量含非数值元素") from exc
            if dimension is None:
                dimension = len(values)
            elif len(values) != dimension:
                raise JevProtocolError(
                    f"embedding 维度不一致: {len(values)} != {dimension}"
                )
            vectors.append(values)
        return vectors

    def close(self) -> None:
        self._client.close()


class EmbeddingService:
    """独立 llama-server embedding 实例（惰性启动，Kernel.close 释放）。

    真实子进程 + 真实 HTTP；启动失败按不可用处理（缓存降级），绝不伪造向量。
    """

    def __init__(
        self, manager: LlamaServerManager | None, client: EmbeddingClient
    ) -> None:
        self._manager = manager
        self._client = client
        self._started = manager is None  # 外部已启动的服务无需拉起

    @property
    def base_url(self) -> str:
        return self._client.base_url

    def available(self) -> bool:
        if not self._ensure_started():
            return False
        return self._client.available()

    def embed(self, texts: list[str] | tuple[str, ...]) -> list[list[float]]:
        if not self._ensure_started():
            raise JevUnavailableError("embedding 服务无法启动")
        return self._client.embed(texts)

    def _ensure_started(self) -> bool:
        if self._started:
            return True
        try:
            if self._manager is not None:
                self._manager.ensure_running()
        except Exception:  # noqa: BLE001 - 启动失败 → 降级（缓存禁用）
            return False
        self._started = True
        return True

    def close(self) -> None:
        try:
            if self._manager is not None:
                self._manager.stop()
        finally:
            self._client.close()
            self._started = self._manager is None


__all__ = ["EmbeddingClient", "EmbeddingService"]
