"""服务应用组装（design.md §4.3.1/§4.4，依赖方向：server → kernel）。

Kernel 不依赖 server（依赖倒置）；完整服务（systemone + chat + /health）
在此组装。/health 契约（design §4.3.1）：
`{status, cerebellum, cerebrum, breaker, uptime}`。
"""

import logging
import time

from fastapi import FastAPI

from jev.kernel.cerebrum.llama_server import LlamaServerClient
from jev.kernel.cerebrum.scheduling import ScheduledClient
from jev.kernel.cerebrum.snapshot import SnapshotClient
from jev.kernel.cerebrum.tiering import TieredBrain
from jev.kernel.kernel import Kernel
from jev.server.chat import create_chat_router
from jev.server.systemone import create_app

logger = logging.getLogger(__name__)


def build_systemone_app(kernel: Kernel) -> FastAPI:
    """挂载 /v1/systemone 的应用（kernel 即熔断内嵌的后端门面）。"""
    return create_app(kernel)


def build_serve_app(kernel: Kernel, *, max_concurrency: int = 8) -> FastAPI:
    """挂载 /v1/systemone + /v1/chat/completions + /health 的完整应用。

    chat 走级联全链路（engine/台账/生成超时注入）；大脑客户端绑定管理器
    端口，管理器未启动时 chat 生成路径自然 503（serve 按需拉起属 T015）。
    """
    app = create_app(kernel)
    brain_client = (
        kernel.supervisor
        if kernel.supervisor is not None
        else (
            LlamaServerClient(
                kernel.brain.base_url,
                cache_prompt=kernel.runtime_config.prefix_cache,
            )
            if kernel.brain is not None
            else None
        )
    )
    snapshot = kernel.snapshot_config
    snapshot_enabled = snapshot.enabled and kernel.supervisor is None
    if snapshot.enabled and kernel.supervisor is not None:
        logger.warning("KV 快照与 supervisor 暂不叠加，本次禁用快照包装")
    elif snapshot_enabled:
        if isinstance(brain_client, LlamaServerClient):
            brain_client = SnapshotClient(
                brain_client, snapshot, kernel.snapshot_namespace_large
            )
    if kernel.slot_scheduler.enabled:
        if isinstance(brain_client, LlamaServerClient):
            brain_client = ScheduledClient(brain_client, kernel.slot_scheduler)
        else:
            logger.warning("当前大脑客户端类型不支持并行槽调度，本次禁用调度包装")
    tiered_brain = None
    if (
        brain_client is not None
        and kernel.small_brain is not None
        and kernel.small_brain.is_running()
    ):
        small_client: LlamaServerClient = LlamaServerClient(
            kernel.small_brain.base_url,
            cache_prompt=kernel.runtime_config.prefix_cache,
        )
        snapshot_client: LlamaServerClient = small_client
        if snapshot_enabled:
            snapshot_client = SnapshotClient(
                small_client, snapshot, kernel.snapshot_namespace_small
            )
        tiered_brain = TieredBrain(brain_client, snapshot_client, kernel.tier_config)
    app.include_router(
        create_chat_router(
            kernel,
            brain_client,
            kernel.registry,
            max_concurrency=max_concurrency,
            engine=kernel.engine,
            ledger=kernel.ledger,
            generation_timeout_s=kernel.generation_timeout_s,
            calibration=kernel.calibration,
            max_brain_tokens=kernel.max_brain_tokens,
            max_latency_ms=kernel.max_latency_ms,
            draft_config=kernel.draft_config,
            runtime_config=kernel.runtime_config,
            cache=kernel.cache,
            embeddings=kernel.embeddings,
            risk_controller=kernel.risk_controller,
            admission_policy=kernel.admission_policy,
            profile_name=(kernel.profile.name if kernel.profile is not None else ""),
            evidence=kernel.evidence_selector,
            stable_compressor=kernel.stable_compressor,
            decision_int8=kernel.decision_int8,
            decision_int8_gates=kernel.decision_int8_gates,
            tiered_brain=tiered_brain,
            extractor=kernel.extractor,
        )
    )

    started = time.monotonic()

    @app.get("/health")
    async def health() -> dict:
        cerebrum_running = (
            kernel.brain.is_running() if kernel.brain is not None else False
        )
        ready = kernel.brain is None or cerebrum_running
        payload = {
            "status": "ok" if ready else "starting",
            "cerebellum": True,
            "cerebrum": cerebrum_running,
            "breaker": kernel.breaker.state.value,
            "uptime": round(time.monotonic() - started, 3),
        }
        if kernel.supervisor is not None:
            payload["supervisor"] = kernel.supervisor.stats()
        return payload

    return app


__all__ = ["build_systemone_app", "build_serve_app"]
