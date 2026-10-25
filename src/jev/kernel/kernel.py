"""Kernel 门面（组合根，design.md §4.3.5/§4.4）。

装配小脑后端/决策注册表/级联引擎/熔断器/大脑管理器，是 server/sdk/cli/mcp
薄壳的唯一进程内入口。system_one 经熔断包装（srs FR6）：OPEN 期间快速失败
（<100ms，JevUnavailableError，不触碰真实后端），成败均记账；成功记录在
滑动窗口内自然冲抵失败。Kernel 自身满足 CerebellumBackend 契约，可直接
喂给 server 侧应用组装（jev.server.app，依赖方向 server → kernel）。

第二后端回退（design 异常流程 7）：主后端为 kev 时构造 Laya 兜底后端，
kev 调用失败（含熔断 OPEN 快速失败）自动回退 Laya 并记台账
backend_fallback=true（kind=fallback 独立条目——Kernel 层无聊天请求
上下文，request_id 为事件自身 id）。
"""

import logging
import time
import tomllib
import uuid
from pathlib import Path

from jev.errors import JevAssetError, JevError, JevUnavailableError
from jev.kernel.cache.semantic import CacheConfig, SemanticCache
from jev.kernel.cascade.breaker import Breaker, BreakerState
from jev.kernel.cascade.draft import DraftConfig
from jev.kernel.cascade.engine import CascadeEngine, RoutePolicy
from jev.kernel.cerebellum.base import CerebellumBackend, Question, SystemOneResponse
from jev.kernel.cerebellum.registry import create_backend, repo_root
from jev.kernel.cerebrum.embeddings import EmbeddingClient, EmbeddingService
from jev.kernel.cerebrum.llama_server import LlamaServerManager, slot_server_args
from jev.kernel.decisions.calibration import Calibration, load_calibration
from jev.kernel.decisions.framework import DecisionPoint, DecisionRegistry
from jev.kernel.ledger.writer import LedgerEntry, LedgerWriter
from jev.kernel.prefill.planner import PrefillConfig
from jev.kernel.runtime.bands import BandPolicy
from jev.kernel.runtime.config import RuntimeConfig
from jev.kernel.runtime.prompt import PromptConfig

logger = logging.getLogger(__name__)


class Kernel(CerebellumBackend):
    """进程内内核：组件经构造注入（DI），from_config 为唯一装配入口。"""

    def __init__(
        self,
        *,
        cerebellum: CerebellumBackend,
        registry: DecisionRegistry,
        breaker: Breaker,
        brain: LlamaServerManager | None = None,
        ledger: LedgerWriter | None = None,
        fallback_cerebellum: CerebellumBackend | None = None,
        generation_timeout_s: float = 120.0,
        calibration: Calibration | None = None,
        policy: RoutePolicy | None = None,
        max_brain_tokens: int | None = None,
        max_latency_ms: float | None = None,
        draft_config: DraftConfig | None = None,
        runtime_config: RuntimeConfig | None = None,
        cache: SemanticCache | None = None,
        embeddings: EmbeddingService | None = None,
    ) -> None:
        self._cerebellum = cerebellum
        self._registry = registry
        self._breaker = breaker
        self._engine = CascadeEngine(registry, policy=policy)
        self._brain = brain
        self._ledger = ledger
        self._fallback = fallback_cerebellum
        self._generation_timeout_s = float(generation_timeout_s)
        self._calibration = calibration if calibration is not None else Calibration()
        self._max_brain_tokens = max_brain_tokens
        self._max_latency_ms = max_latency_ms
        self._draft_config = draft_config if draft_config is not None else DraftConfig()
        self._runtime_config = runtime_config if runtime_config is not None else RuntimeConfig()
        self._cache = cache
        self._embeddings = embeddings

    # ------------------------------------------------------------------
    # 组合根：configs/jev.toml 单源装配
    # ------------------------------------------------------------------

    @classmethod
    def from_config(
        cls,
        config_path: str | Path,
        *,
        decision_points_path: str | Path | None = None,
    ) -> "Kernel":
        """按 jev.toml 装配（decision-points.toml 默认取同目录，可显式指定）。

        大脑管理器仅构造不启动（serve 按需拉起属 T015）；资产缺失在
        小脑构造期即抛 JevAssetError（fail-fast）。
        """
        config_file = Path(config_path)
        if not config_file.is_file():
            raise JevAssetError(f"配置文件不存在: {config_file}")
        try:
            config = tomllib.loads(config_file.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as exc:
            raise JevAssetError(f"配置文件不是合法 TOML（{config_file}）: {exc}") from exc

        cerebellum_cfg = config.get("cerebellum", {})
        backend_name = cerebellum_cfg.get("default_backend", "laya_onnx")
        backend = create_backend(backend_name, config_file)

        # 异常流程 7：kev 主后端不可用 → 回退默认 Laya（资产就位才装配）
        fallback_backend = None
        if backend_name == "kev":
            laya_model = repo_root() / "models" / "laya" / "laya.onnx"
            if laya_model.is_file():
                fallback_backend = create_backend("laya_onnx", config_file)
            else:
                logger.warning("kev 主后端无 Laya 兜底资产（%s），失败将直接上抛", laya_model)

        breaker_cfg = config.get("breaker", {})
        breaker = Breaker(
            failure_threshold=int(breaker_cfg.get("failure_threshold", 3)),
            window_s=float(breaker_cfg.get("window_s", 60.0)),
            cooldown_s=float(breaker_cfg.get("cooldown_s", 30.0)),
            cooldown_max_s=float(breaker_cfg.get("cooldown_max_s", 120.0)),
        )

        points_path = (
            Path(decision_points_path)
            if decision_points_path is not None
            else config_file.parent / "decision-points.toml"
        )
        registry = DecisionRegistry.from_toml(points_path)

        brain = _build_brain_manager(config)
        ledger = _build_ledger(config)
        cascade_cfg = config.get("cascade", {})
        laya_cfg = cerebellum_cfg.get("laya_onnx", {})
        if not isinstance(laya_cfg, dict):
            laya_cfg = {}
        calibration = load_calibration(laya_cfg.get("calibration_path"))
        policy = RoutePolicy(
            capability_gate=bool(cascade_cfg.get("capability_gate", True)),
            light_requires_low_complexity=bool(
                cascade_cfg.get("light_requires_low_complexity", False)
            ),
        )
        max_brain_tokens = cascade_cfg.get("max_brain_tokens")
        max_latency_ms = cascade_cfg.get("max_latency_ms")
        draft_config = DraftConfig(
            enabled=bool(cascade_cfg.get("draft_injection", False)),
            keep_turns=int(cascade_cfg.get("keep_turns", 2)),
        )
        runtime_config, cache = _build_runtime(config)
        embeddings = _build_embeddings(config)
        return cls(
            cerebellum=backend,
            registry=registry,
            breaker=breaker,
            brain=brain,
            ledger=ledger,
            fallback_cerebellum=fallback_backend,
            generation_timeout_s=float(cascade_cfg.get("generation_timeout_s", 120.0)),
            calibration=calibration,
            policy=policy,
            max_brain_tokens=(
                int(max_brain_tokens) if max_brain_tokens is not None else None
            ),
            max_latency_ms=(
                float(max_latency_ms) if max_latency_ms is not None else None
            ),
            draft_config=draft_config,
            runtime_config=runtime_config,
            cache=cache,
            embeddings=embeddings,
        )

    # ------------------------------------------------------------------
    # 组件访问（薄壳/测试只读入口）
    # ------------------------------------------------------------------

    @property
    def cerebellum(self) -> CerebellumBackend:
        return self._cerebellum

    @property
    def registry(self) -> DecisionRegistry:
        return self._registry

    @property
    def engine(self) -> CascadeEngine:
        return self._engine

    @property
    def breaker(self) -> Breaker:
        return self._breaker

    @property
    def brain(self) -> LlamaServerManager | None:
        return self._brain

    @property
    def ledger(self) -> LedgerWriter | None:
        return self._ledger

    @property
    def fallback_cerebellum(self) -> CerebellumBackend | None:
        return self._fallback

    @property
    def max_brain_tokens(self) -> int | None:
        """请求级 brain token 预算（[cascade] max_brain_tokens；None 不限）。"""
        return self._max_brain_tokens

    @property
    def max_latency_ms(self) -> float | None:
        """请求级延迟预算（[cascade] max_latency_ms；None 不限）。"""
        return self._max_latency_ms

    @property
    def draft_config(self) -> DraftConfig:
        """PRE 草稿注入配置（默认关闭）。"""
        return self._draft_config

    @property
    def runtime_config(self) -> RuntimeConfig:
        """AR003 运行时配置（默认关闭）。"""
        return self._runtime_config

    @property
    def cache(self) -> SemanticCache | None:
        """语义缓存实例（启用时非空）。"""
        return self._cache

    @property
    def embeddings(self) -> EmbeddingService | None:
        """真实 embedding 服务（未配置时为 None）。"""
        return self._embeddings

    @property
    def generation_timeout_s(self) -> float:
        """路径②/③ 生成总时长上限（EX-04，[cascade] 单源）。"""
        return self._generation_timeout_s

    @property
    def calibration(self) -> Calibration:
        """AR002：逐点温度/τ/margin 校准（缺失时为空校准）。"""
        return self._calibration

    def register_point(self, point: DecisionPoint) -> None:
        """编程注册决策点（SDK 扩展点，FR10：与 TOML 声明等价）。"""
        self._registry.register(point)

    # ------------------------------------------------------------------
    # CerebellumBackend 契约（熔断包装 + 第二后端回退）
    # ------------------------------------------------------------------

    def system_one(self, state: str, questions: dict[str, Question]) -> SystemOneResponse:
        """熔断包装的单次前向多问；OPEN 快速失败（FR6 <100ms 口径）。

        主后端失败（含 OPEN 快速失败）且有兜底后端时回退 Laya 并记台账
        backend_fallback=true（异常流程 7）；熔断成败仍按主后端口径记账。
        """
        try:
            return self._system_one_primary(state, questions)
        except JevError as exc:
            if self._fallback is None:
                raise
            logger.warning("小脑主后端失败，回退 Laya 兜底后端: %s", exc)
            started = time.perf_counter()
            response = self._fallback.system_one(state, questions)
            self._log_backend_fallback(exc, (time.perf_counter() - started) * 1000)
            return response

    def _system_one_primary(
        self, state: str, questions: dict[str, Question]
    ) -> SystemOneResponse:
        if not self._breaker.allow():
            raise JevUnavailableError("小脑熔断 OPEN（冷却中），本次请求快速失败")
        try:
            response = self._cerebellum.system_one(state, questions)
        except JevError:
            self._breaker.record(False)
            raise
        self._breaker.record(True)
        return response

    def _log_backend_fallback(self, exc: JevError, latency_ms: float) -> None:
        """台账 backend_fallback=true 标记（独立事件条目，异常流程 7）。"""
        if self._ledger is None:
            return
        self._ledger.log(
            LedgerEntry(
                request_id=uuid.uuid4().hex,
                kind="fallback",
                ts=time.time(),
                backend_fallback=True,
                value=self._cerebellum.name(),
                applied=self._fallback.name() if self._fallback is not None else None,
                breaker_state=self._breaker.state.value
                if isinstance(self._breaker.state, BreakerState)
                else str(self._breaker.state),
                latency_ms=round(latency_ms, 3),
            )
        )

    def name(self) -> str:
        return self._cerebellum.name()

    def warmup(self) -> None:
        self._cerebellum.warmup()

    def close(self) -> None:
        """释放底层资源（大脑子进程/台账写线程）；幂等。"""
        if self._brain is not None:
            self._brain.stop()
        if self._ledger is not None:
            self._ledger.close()

    def __enter__(self) -> "Kernel":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def _build_ledger(config: dict) -> LedgerWriter | None:
    """[ledger] 存在则构造台账写入器（异步 JSONL，主路径非阻塞）；相对路径以仓库根解析。"""
    ledger_cfg = config.get("ledger")
    if not ledger_cfg:
        return None
    ledger_dir = Path(ledger_cfg.get("dir", "data/ledger"))
    if not ledger_dir.is_absolute():
        ledger_dir = repo_root() / ledger_dir
    return LedgerWriter(
        dir=ledger_dir,
        queue_maxsize=int(ledger_cfg.get("queue_maxsize", 10000)),
        warn_after_failures=int(ledger_cfg.get("warn_after_failures", 100)),
    )


def _build_brain_manager(config: dict) -> LlamaServerManager | None:
    """[cerebrum] 存在则构造大脑管理器（不启动）；相对路径以仓库根解析。"""
    cerebrum_cfg = config.get("cerebrum")
    if not cerebrum_cfg:
        return None

    def _resolve(path: str) -> Path:
        resolved = Path(path)
        return resolved if resolved.is_absolute() else repo_root() / resolved

    default_bin = "models/llama-server/bin/llama-server.exe"
    default_gguf = "models/qwen/qwen2.5-1.5b-instruct-q4_k_m.gguf"
    return LlamaServerManager(
        llama_server_bin=_resolve(cerebrum_cfg.get("llama_server_bin", default_bin)),
        gguf_path=_resolve(cerebrum_cfg.get("gguf_path", default_gguf)),
        host=cerebrum_cfg.get("host", "127.0.0.1"),
        port=int(cerebrum_cfg.get("port", 8388)),
        ctx_size=int(cerebrum_cfg.get("ctx_size", 4096)),
        startup_probe=cerebrum_cfg.get("startup_probe", "/health"),
        restart_max=int(cerebrum_cfg.get("restart_max", 3)),
        extra_args=slot_server_args(
            parallel=int(cerebrum_cfg.get("parallel", 1)),
            slot_prompt_similarity=cerebrum_cfg.get("slot_prompt_similarity"),
        ),
    )


def _build_runtime(config: dict) -> tuple[RuntimeConfig, SemanticCache | None]:
    """[runtime]/[cache]/[prefill] → RuntimeConfig + 缓存实例（默认关闭）。"""
    runtime_cfg = config.get("runtime", {}) or {}
    cache_cfg = config.get("cache", {}) or {}
    prefill_cfg = config.get("prefill", {}) or {}
    bands = BandPolicy(
        tau_low=float(runtime_cfg.get("tau_low", 0.35)),
        tau_high=float(runtime_cfg.get("tau_high", 0.65)),
        verifier_enabled=bool(runtime_cfg.get("verifier_enabled", False)),
        self_verify_mode=str(runtime_cfg.get("self_verify_mode", "observe")),
        gate_threshold=float(runtime_cfg.get("gate_threshold", 0.60)),
    )
    cache_config = CacheConfig(
        enabled=bool(cache_cfg.get("enabled", False)),
        similarity=float(cache_cfg.get("similarity", 0.93)),
        margin=float(cache_cfg.get("margin", 0.02)),
        ttl_s=float(cache_cfg.get("ttl_s", 1800.0)),
        capacity=int(cache_cfg.get("capacity", 256)),
    )
    prefill = PrefillConfig(
        enabled=bool(prefill_cfg.get("enabled", False)),
        budget_ratio=float(prefill_cfg.get("budget_ratio", 0.6)),
        min_prompt_tokens=int(prefill_cfg.get("min_prompt_tokens", 512)),
        min_blocks=int(prefill_cfg.get("min_blocks", 4)),
        block_tokens=int(prefill_cfg.get("block_tokens", 256)),
    )
    prompt = PromptConfig(
        draft_enabled=bool(runtime_cfg.get("draft_injection", False)),
        memory_enabled=bool(runtime_cfg.get("memory_enabled", False)),
        prefill_enabled=prefill.enabled,
        keep_turns=int(runtime_cfg.get("keep_turns", 2)),
    )
    runtime_config = RuntimeConfig(
        enabled=bool(runtime_cfg.get("enabled", False)),
        bands=bands,
        cache=cache_config,
        prefill=prefill,
        prompt=prompt,
        verifier_max_tokens=int(runtime_cfg.get("verifier_max_tokens", 16)),
    )
    cache = SemanticCache(cache_config) if cache_config.enabled else None
    return runtime_config, cache


def _build_embeddings(config: dict) -> EmbeddingService | None:
    """[embeddings]：base_url 直连或独立子进程（惰性启动）；空则禁用。"""
    embeddings_cfg = config.get("embeddings", {}) or {}
    base_url = str(embeddings_cfg.get("base_url", "") or "").strip()
    model = str(embeddings_cfg.get("model", "local"))
    timeout_s = float(embeddings_cfg.get("timeout_s", 10.0))
    if base_url:
        return EmbeddingService(
            None, EmbeddingClient(base_url, model=model, timeout_s=timeout_s)
        )
    model_path = str(embeddings_cfg.get("model_path", "") or "").strip()
    if not model_path:
        return None
    cerebrum_cfg = config.get("cerebrum", {}) or {}

    def _resolve(path: str) -> Path:
        resolved = Path(path)
        return resolved if resolved.is_absolute() else repo_root() / resolved

    port = int(embeddings_cfg.get("port", 8389))
    manager = LlamaServerManager(
        llama_server_bin=_resolve(
            cerebrum_cfg.get(
                "llama_server_bin", "models/llama-server/bin/llama-server.exe"
            )
        ),
        gguf_path=_resolve(model_path),
        host=cerebrum_cfg.get("host", "127.0.0.1"),
        port=port,
        ctx_size=int(embeddings_cfg.get("ctx_size", 2048)),
        startup_probe="/health",
        restart_max=int(embeddings_cfg.get("restart_max", 1)),
        extra_args=["--embeddings", "--pooling", "mean"],
    )
    client = EmbeddingClient(
        f"http://{cerebrum_cfg.get('host', '127.0.0.1')}:{port}",
        model=model,
        timeout_s=timeout_s,
    )
    return EmbeddingService(manager, client)
