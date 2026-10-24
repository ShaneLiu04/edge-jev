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
from jev.kernel.cascade.breaker import Breaker, BreakerState
from jev.kernel.cascade.engine import CascadeEngine
from jev.kernel.cerebellum.base import CerebellumBackend, Question, SystemOneResponse
from jev.kernel.cerebellum.registry import create_backend, repo_root
from jev.kernel.cerebrum.llama_server import LlamaServerManager
from jev.kernel.decisions.framework import DecisionPoint, DecisionRegistry
from jev.kernel.ledger.writer import LedgerEntry, LedgerWriter

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
    ) -> None:
        self._cerebellum = cerebellum
        self._registry = registry
        self._breaker = breaker
        self._engine = CascadeEngine(registry)
        self._brain = brain
        self._ledger = ledger
        self._fallback = fallback_cerebellum
        self._generation_timeout_s = float(generation_timeout_s)

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
        return cls(
            cerebellum=backend,
            registry=registry,
            breaker=breaker,
            brain=brain,
            ledger=ledger,
            fallback_cerebellum=fallback_backend,
            generation_timeout_s=float(cascade_cfg.get("generation_timeout_s", 120.0)),
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
    def generation_timeout_s(self) -> float:
        """路径②/③ 生成总时长上限（EX-04，[cascade] 单源）。"""
        return self._generation_timeout_s

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
    )
