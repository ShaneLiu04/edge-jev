"""T011-c：Kernel 组合根测试——真实组件装配 + system_one 熔断包装（FR6）。

约束 C1：禁 mock。小脑用 conftest 微型真实契约 bundle（真 ONNX 前向）；
失败/恢复场景用真实网络行为（已释放端口连接拒绝 / 真本地 HTTP 服务器）；
决策注册表用真实 configs/decision-points.toml（18 点声明式配置）。
"""

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jev.errors import JevAssetError, JevProtocolError, JevUnavailableError
from jev.kernel.cascade.breaker import Breaker, BreakerState
from jev.kernel.cascade.engine import CascadeEngine
from jev.kernel.cerebellum.base import (
    ChoiceAnswer,
    ChoiceQuestion,
    NoulAnswer,
    NoulQuestion,
    ScoreAnswer,
    ScoreQuestion,
)
from jev.kernel.cerebellum.kev_http import KevHttpBackend
from jev.kernel.cerebellum.wire import encode_request
from jev.kernel.decisions.framework import DecisionRegistry
from jev.kernel.kernel import Kernel
from jev.kernel.ledger.query import LedgerQuery
from jev.kernel.ledger.writer import LedgerWriter
from jev.server.app import build_systemone_app

REPO = Path(__file__).resolve().parents[2]
STATE = "hello world jev"


def _free_port() -> int:
    """取一个当前空闲端口（bind 后立即释放，制造真实连接拒绝/后续可占用）。"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _choice_question() -> ChoiceQuestion:
    return ChoiceQuestion(choices={"first": None, "last": None}, instructions="pick one")


# ---------------------------------------------------------------------------
# 组合根 from_config（真 TOML 配置 + 微型真实 bundle 绝对路径）
# ---------------------------------------------------------------------------


def _write_tiny_jev_toml(
    tmp_path, model_path, tokenizer_path, config_path, *, failure_threshold: int = 3
) -> Path:
    """微型 jev.toml：绝对路径指向 conftest 微型 bundle；无 [cerebrum]（大脑不装配）。"""
    # TOML 基本串内反斜杠是转义符——统一 posix 斜杠
    model_posix = Path(model_path).as_posix()
    tokenizer_posix = Path(tokenizer_path).as_posix()
    config_posix = Path(config_path).as_posix()
    toml = f"""
[cerebellum]
default_backend = "laya_onnx"

[cerebellum.laya_onnx]
model_path = "{model_posix}"
tokenizer_path = "{tokenizer_posix}"
config_path = "{config_posix}"

[breaker]
window_s = 60
failure_threshold = {failure_threshold}
cooldown_s = 30
cooldown_max_s = 120
"""
    path = tmp_path / "jev.toml"
    path.write_text(toml, encoding="utf-8")
    return path


@pytest.fixture()
def registry() -> DecisionRegistry:
    return DecisionRegistry.from_toml(REPO / "configs" / "decision-points.toml")


@pytest.fixture()
def kernel(make_backend, registry) -> Kernel:
    return Kernel(
        cerebellum=make_backend(),
        registry=registry,
        breaker=Breaker(failure_threshold=3, window_s=60.0, cooldown_s=30.0),
    )


class TestFromConfig:
    def _kernel(
        self,
        tmp_path,
        model_path,
        tokenizer_path,
        config_path,
        *,
        failure_threshold: int = 3,
    ) -> Kernel:
        config = _write_tiny_jev_toml(
            tmp_path,
            model_path,
            tokenizer_path,
            config_path,
            failure_threshold=failure_threshold,
        )
        return Kernel.from_config(
            config, decision_points_path=REPO / "configs" / "decision-points.toml"
        )

    def test_composes_real_components(
        self, tmp_path, model_path, tokenizer_path, config_path
    ):
        kernel = self._kernel(tmp_path, model_path, tokenizer_path, config_path)
        assert kernel.cerebellum.name() == "laya_onnx"
        assert isinstance(kernel.engine, CascadeEngine)
        assert kernel.breaker.state is BreakerState.CLOSED
        # 真实 18 点声明式配置装配
        assert len(list(kernel.registry.ids)) == 18
        # 无 [cerebrum] 时大脑侧不装配（serve 按需拉起属 T015）
        assert kernel.brain is None

    def test_unknown_backend_rejected(self, tmp_path, model_path, tokenizer_path, config_path):
        config = _write_tiny_jev_toml(tmp_path, model_path, tokenizer_path, config_path)
        text = config.read_text(encoding="utf-8").replace("laya_onnx", "nonsense")
        config.write_text(text, encoding="utf-8")
        with pytest.raises(JevProtocolError, match="未知小脑后端"):
            Kernel.from_config(
                config, decision_points_path=REPO / "configs" / "decision-points.toml"
            )

    def test_missing_config_rejected(self, tmp_path):
        with pytest.raises(JevAssetError, match="配置文件不存在"):
            Kernel.from_config(tmp_path / "nope.toml")

    def test_breaker_params_from_config(
        self, tmp_path, model_path, tokenizer_path, config_path
    ):
        kernel = self._kernel(
            tmp_path, model_path, tokenizer_path, config_path, failure_threshold=5
        )
        # 阈值来自配置：4 次真失败（连接拒绝）后仍 CLOSED，第 5 次 OPEN
        port = _free_port()
        dead = Kernel(
            cerebellum=KevHttpBackend(serve_url=f"http://127.0.0.1:{port}"),
            registry=kernel.registry,
            breaker=kernel.breaker,
        )
        question = _choice_question()
        for _ in range(4):
            with pytest.raises(JevUnavailableError):
                dead.system_one(STATE, {"q": question})
        assert dead.breaker.state is BreakerState.CLOSED
        with pytest.raises(JevUnavailableError):
            dead.system_one(STATE, {"q": question})
        assert dead.breaker.state is BreakerState.OPEN


# ---------------------------------------------------------------------------
# system_one：真前向透传 + 熔断记录
# ---------------------------------------------------------------------------


class TestSystemOnePassthrough:
    def test_real_forward_three_question_types(self, kernel):
        response = kernel.system_one(
            STATE,
            {
                "q_choice": _choice_question(),
                "q_noul": NoulQuestion(instructions="is it needed"),
                "q_score": ScoreQuestion(
                    criteria=["low", "mid", "high"], instructions="rate it"
                ),
            },
        )
        # toy 契约语义：choice 恒选后出现的选项（marker 位置大者胜）
        choice = response.answers["q_choice"]
        assert isinstance(choice, ChoiceAnswer)
        assert choice.choice == "last"
        assert isinstance(response.answers["q_noul"], NoulAnswer)
        assert isinstance(response.answers["q_score"], ScoreAnswer)
        assert response.input_tokens > 0
        # 成功调用不改变熔断闭合态
        assert kernel.breaker.state is BreakerState.CLOSED

    def test_name_and_warmup_passthrough(self, kernel):
        assert kernel.name() == "laya_onnx"
        kernel.warmup()  # 一次真实前向，不抛即通过
        assert kernel.breaker.state is BreakerState.CLOSED


# ---------------------------------------------------------------------------
# 熔断包装：真实连接拒绝 ×3 → OPEN → 快速失败（<100ms）
# ---------------------------------------------------------------------------


class TestBreakerWrap:
    @pytest.fixture()
    def dead_kernel(self, registry) -> Kernel:
        port = _free_port()
        return Kernel(
            cerebellum=KevHttpBackend(serve_url=f"http://127.0.0.1:{port}"),
            registry=registry,
            breaker=Breaker(failure_threshold=3, window_s=60.0, cooldown_s=30.0),
        )

    def _fail_once(self, kernel: Kernel) -> None:
        with pytest.raises(JevUnavailableError):
            kernel.system_one(STATE, {"q": _choice_question()})

    def test_three_real_failures_open(self, dead_kernel):
        for _ in range(3):
            self._fail_once(dead_kernel)
        assert dead_kernel.breaker.state is BreakerState.OPEN

    def test_open_fails_fast_under_100ms(self, dead_kernel):
        for _ in range(3):
            self._fail_once(dead_kernel)
        start = time.perf_counter()
        with pytest.raises(JevUnavailableError, match="熔断"):
            dead_kernel.system_one(STATE, {"q": _choice_question()})
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 100, f"熔断快速失败耗时 {elapsed_ms:.1f}ms（FR6 要求 <100ms）"

    def test_half_open_recovery_with_real_server(self, dead_kernel, registry):
        """OPEN → clock 推进冷却到期 → 真服务器上线 → 探测成功闭合。"""
        port = _free_port()
        # 用可注入 clock 的重建 breaker 复用同一真实后端端口
        clock = {"now": 0.0}
        backend = KevHttpBackend(serve_url=f"http://127.0.0.1:{port}")
        kernel = Kernel(
            cerebellum=backend,
            registry=registry,
            breaker=Breaker(
                failure_threshold=3,
                window_s=60.0,
                cooldown_s=30.0,
                clock=lambda: clock["now"],
            ),
        )
        for _ in range(3):
            self._fail_once(kernel)
        assert kernel.breaker.state is BreakerState.OPEN

        clock["now"] += 31.0  # 冷却到期 → HALF_OPEN 放行探测

        server = _serve_systemone(port)
        try:
            response = kernel.system_one(STATE, {"q": _choice_question()})
            assert isinstance(response.answers["q"], ChoiceAnswer)
        finally:
            server.shutdown()
            server.server_close()
        assert kernel.breaker.state is BreakerState.CLOSED


# ---------------------------------------------------------------------------
# 端点组装：Kernel 即 CerebellumBackend 门面（熔断内嵌的 /v1/systemone）
# ---------------------------------------------------------------------------


class TestSystemoneApp:
    def test_serves_real_forward(self, kernel):
        client = TestClient(build_systemone_app(kernel))
        payload = encode_request("local", STATE, {"q": _choice_question()})
        response = client.post("/v1/systemone", json=payload)
        assert response.status_code == 200
        body = response.json()
        assert body["answers"]["q"]["choice"] == "last"
        assert body["model"] == "laya_onnx"

    def test_503_fast_after_breaker_opens(self, registry):
        port = _free_port()
        kernel = Kernel(
            cerebellum=KevHttpBackend(serve_url=f"http://127.0.0.1:{port}"),
            registry=registry,
            breaker=Breaker(failure_threshold=3, window_s=60.0, cooldown_s=30.0),
        )
        client = TestClient(build_systemone_app(kernel))
        payload = encode_request("local", STATE, {"q": _choice_question()})
        for _ in range(3):
            assert client.post("/v1/systemone", json=payload).status_code == 503
        start = time.perf_counter()
        response = client.post("/v1/systemone", json=payload)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert response.status_code == 503
        assert response.headers.get("Retry-After") == "1"
        assert elapsed_ms < 100, f"熔断期 503 耗时 {elapsed_ms:.1f}ms（FR6 要求 <100ms）"


# ---------------------------------------------------------------------------
# EX-07（异常流程 7）：kev 主后端失败 → Laya 兜底 + 台账 backend_fallback
# ---------------------------------------------------------------------------


class TestBackendFallback:
    @pytest.fixture()
    def fallback_setup(self, make_backend, registry, tmp_path):
        port = _free_port()  # 已释放端口 → 真实连接拒绝（kev 不可用的真实形态）
        ledger = LedgerWriter(dir=tmp_path / "ledger")
        kernel = Kernel(
            cerebellum=KevHttpBackend(serve_url=f"http://127.0.0.1:{port}"),
            registry=registry,
            breaker=Breaker(failure_threshold=3, window_s=60.0, cooldown_s=30.0),
            ledger=ledger,
            fallback_cerebellum=make_backend(),
        )
        yield kernel, ledger, tmp_path / "ledger"
        ledger.close()

    def test_primary_failure_falls_back_to_laya(self, fallback_setup):
        kernel, ledger, ledger_dir = fallback_setup
        # 主后端连接拒绝 → 兜底 Laya 真前向成功应答
        response = kernel.system_one(STATE, {"q": _choice_question()})
        assert response.answers["q"].choice == "last"
        # 台账记录 backend_fallback=true 事件条目
        ledger.flush()
        entries = list(LedgerQuery(dir=ledger_dir).iter_entries())
        fallback_entries = [e for e in entries if e.kind == "fallback"]
        assert len(fallback_entries) == 1
        entry = fallback_entries[0]
        assert entry.backend_fallback is True
        assert entry.value == "kev"
        assert entry.applied == "laya_onnx"
        # 熔断按主后端口径记账：一次失败仍 CLOSED
        assert kernel.breaker.state is BreakerState.CLOSED

    def test_open_fast_fail_also_served_by_fallback(self, fallback_setup):
        kernel, ledger, _ = fallback_setup
        for _ in range(3):
            response = kernel.system_one(STATE, {"q": _choice_question()})
            assert response.answers["q"].choice == "last"
        assert kernel.breaker.state is BreakerState.OPEN
        # OPEN 快速失败同样走兜底：服务连续性不中断（含兜底真前向，总耗时按
        # FR6 口径断言快速失败部分本身——为不掺入 ONNX 前向噪声，只断言不抛
        # JevUnavailableError 且状态保持 OPEN；<100ms 口径由 TestBreakerSystemone
        # 纯 503 路径覆盖）
        response = kernel.system_one(STATE, {"q": _choice_question()})
        assert response.answers["q"].choice == "last"
        assert kernel.breaker.state is BreakerState.OPEN

    def test_no_fallback_propagates_error(self, registry):
        port = _free_port()
        kernel = Kernel(
            cerebellum=KevHttpBackend(serve_url=f"http://127.0.0.1:{port}"),
            registry=registry,
            breaker=Breaker(failure_threshold=3, window_s=60.0, cooldown_s=30.0),
        )
        with pytest.raises(JevUnavailableError):
            kernel.system_one(STATE, {"q": _choice_question()})


# ---------------------------------------------------------------------------
# 真本地 HTTP 服务器（真实协议对端，非 mock 模型）
# ---------------------------------------------------------------------------

_VALID_RESPONSE = {
    "answers": {
        "q": {
            "type": "choice",
            "choice": "last",
            "probabilities": {"first": 0.25, "last": 0.75},
        }
    },
    "usage": {"input_tokens": 8},
}


class _SystemOneHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        body = json.dumps(_VALID_RESPONSE).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _serve_systemone(port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), _SystemOneHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
