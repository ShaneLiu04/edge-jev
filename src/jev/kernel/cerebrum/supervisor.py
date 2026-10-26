"""大脑自愈监督器（AR006 FR6-3，design §2.4）。

只处理「进程不健康/调用超时/不可达」：健康探针失败 → 重启子进程 → 重试一次。
协议错误（4xx 等）不重启；恢复失败显式抛错，绝不返回伪造内容。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Any

import httpx

from jev.errors import (
    BrainStartupError,
    JevError,
    JevTimeoutError,
    JevUnavailableError,
)
from jev.kernel.cerebrum.llama_server import LlamaServerClient, LlamaServerManager

logger = logging.getLogger(__name__)

_RECOVERY_WINDOW_S = 3600.0


class BrainSupervisor:
    """真实 llama-server 的健康探针 + 崩溃重启 + 单次重试包装。"""

    def __init__(
        self,
        *,
        manager: LlamaServerManager | None = None,
        client: LlamaServerClient | None = None,
        health_interval_s: float = 30.0,
        health_timeout_s: float = 3.0,
        max_recoveries: int = 5,
        recovery_cooldown_s: float = 5.0,
    ) -> None:
        if client is None:
            if manager is None:
                raise ValueError("BrainSupervisor 需要 manager 或 client 至少一个")
            client = LlamaServerClient(manager.base_url)
        self._manager = manager
        self._client = client
        self._health_interval_s = max(0.0, float(health_interval_s))
        self._health_timeout_s = float(health_timeout_s)
        self._max_recoveries = max(1, int(max_recoveries))
        self._recovery_cooldown_s = max(0.0, float(recovery_cooldown_s))
        self._last_probe = 0.0
        self._recoveries: list[float] = []
        self._restarts = 0
        self._retries = 0
        self._last_recovery_ms = 0.0
        self._last_healthy: bool | None = None

    @property
    def base_url(self) -> str:
        return self._manager.base_url if self._manager is not None else self._client.base_url

    def health(self) -> bool:
        try:
            response = httpx.get(
                f"{self.base_url}/health", timeout=self._health_timeout_s
            )
        except httpx.HTTPError:
            self._last_healthy = False
            return False
        healthy = 200 <= response.status_code < 300
        self._last_healthy = healthy
        return healthy

    def stats(self) -> dict[str, Any]:
        return {
            "restarts": self._restarts,
            "retries": self._retries,
            "recoveries": len(self._recoveries),
            "last_recovery_ms": round(self._last_recovery_ms, 3),
            "healthy": self._last_healthy,
            "managed": self._manager is not None,
        }

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 512,
        temperature: float = 0.7,
        stop: list[str] | None = None,
        timeout_s: float | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._ensure_healthy()
        try:
            return self._client.chat(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stop=stop,
                timeout_s=timeout_s,
                extra_body=extra_body,
            )
        except (JevTimeoutError, JevUnavailableError) as exc:
            if self._manager is None:
                raise
            logger.warning("大脑调用失败，尝试自愈后重试一次: %s", exc)
            self._retries += 1
            self._recover(force=True)
            return self._client.chat(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stop=stop,
                timeout_s=timeout_s,
                extra_body=extra_body,
            )

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 512,
        temperature: float = 0.7,
        stop: list[str] | None = None,
        timeout_s: float | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        self._ensure_healthy()
        return self._client.chat_stream(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=stop,
            timeout_s=timeout_s,
            extra_body=extra_body,
        )

    def close(self) -> None:
        self._client.close()

    def _ensure_healthy(self) -> None:
        now = time.monotonic()
        if (
            self._health_interval_s > 0.0
            and self._last_probe > 0.0
            and now - self._last_probe < self._health_interval_s
        ):
            return
        self._last_probe = now
        if self.health():
            return
        logger.warning("大脑健康探针失败（%s），尝试恢复", self.base_url)
        self._recover(force=True)

    def _recover(self, *, force: bool) -> None:
        if self._manager is None:
            raise JevUnavailableError("大脑不可达且无托管进程可重启")
        now = time.monotonic()
        cutoff = now - _RECOVERY_WINDOW_S
        self._recoveries = [item for item in self._recoveries if item >= cutoff]
        if force:
            recent = self._recoveries[-1] if self._recoveries else None
            if recent is not None and now - recent < self._recovery_cooldown_s:
                logger.warning("距上次恢复不足 %.1fs，跳过重启", self._recovery_cooldown_s)
                return
        if len(self._recoveries) >= self._max_recoveries:
            raise BrainStartupError(
                f"大脑一小时内恢复次数超限（{len(self._recoveries)}/{self._max_recoveries}）"
            )
        started = time.perf_counter()
        try:
            self._manager.stop()
            self._manager.start()
        except JevError as exc:
            raise BrainStartupError(f"大脑重启失败: {exc}") from exc
        elapsed = (time.perf_counter() - started) * 1000.0
        self._last_recovery_ms = elapsed
        self._restarts += 1
        self._recoveries.append(now)
        self._last_probe = time.monotonic()
        self._last_healthy = True
        logger.info("大脑已重启（%.0fms）", elapsed)


__all__ = ["BrainSupervisor"]
