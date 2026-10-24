"""T016：Python SDK——JevClient 同步/异步 + 类型化结果 + 编程注册（FR10）。

约束 C1：禁 mock。服务端为真实 FastAPI 应用（微型契约 bundle 小脑）由
真实 uvicorn 线程托管，SDK 经真实 HTTP socket 访问；异常路径用真实
死端口（连接拒绝）与服务端真实 400 校验。
"""

import asyncio
import socket
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI

from jev.errors import JevProtocolError, JevUnavailableError
from jev.kernel.cascade.breaker import Breaker
from jev.kernel.cascade.engine import CascadeEngine
from jev.kernel.cerebellum.laya_onnx import LayaOnnxBackend
from jev.kernel.decisions.framework import DecisionPoint, DecisionRegistry, Mode, Stage
from jev.kernel.kernel import Kernel
from jev.sdk import (
    ChatResult,
    ChoiceResult,
    JevClient,
    NoulResult,
    ScoreResult,
    register_point,
)
from jev.server.chat import create_chat_router
from jev.server.systemone import create_app

STATE = "用一句话介绍你自己"
CHOICES = {"direct": "小脑直答", "cascade": "级联大脑"}
LEVELS = ["low", "mid", "high"]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _point(point_id: str, stage: Stage, options: tuple[str, ...], *, tau: float) -> DecisionPoint:
    """期望 applied=末项（conf>=tau 取末项 / conf<tau 取 fallback，双保险）。"""
    return DecisionPoint(
        id=point_id, stage=stage, question=f"{point_id}: {options[0]} or {options[-1]}?",
        tau=tau, fallback_action=options[-1], question_type="choice", options=options,
    )


def _direct_registry() -> DecisionRegistry:
    """intent→decision → chat direct 路由（无需大脑，4 个 active PRE 点）。"""
    registry = DecisionRegistry()
    registry.register(_point("intent", Stage.PRE, ("simple", "decision"), tau=0.7))
    registry.register(_point("needs_reasoning", Stage.PRE, ("reasoning", "no_reasoning"), tau=0.6))
    registry.register(_point("complexity", Stage.PRE, ("high", "low"), tau=0.6))
    registry.register(_point("safety", Stage.PRE, ("unsafe", "safe"), tau=0.8))
    return registry


@pytest.fixture()
def cerebellum(model_path, tokenizer_path, config_path):
    return LayaOnnxBackend(
        model_path=model_path, tokenizer_path=tokenizer_path, config_path=config_path
    )


@pytest.fixture()
def server_url(cerebellum):
    """真实 uvicorn 线程托管 systemone + chat（direct 路由，无需大脑）。"""
    registry = _direct_registry()
    app: FastAPI = create_app(cerebellum)
    app.include_router(
        create_chat_router(cerebellum, None, registry, engine=CascadeEngine(registry))
    )
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.3)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.1)
    else:
        pytest.fail("uvicorn 测试服务器未就绪")
    try:
        yield url
    finally:
        server.should_exit = True
        thread.join(timeout=10)


class TestJevClientSync:
    def test_decide_returns_typed_choice_result(self, server_url):
        with JevClient(server_url) as client:
            result = client.decide(STATE, CHOICES, "选择应答路径")
        assert isinstance(result, ChoiceResult)
        assert result.choice in CHOICES
        assert 0.0 <= result.confidence <= 1.0
        assert set(result.probabilities) == set(CHOICES)

    def test_judge_returns_typed_noul_result(self, server_url):
        with JevClient(server_url) as client:
            result = client.judge(STATE, "该陈述是否为真？")
        assert isinstance(result, NoulResult)
        assert 0.0 <= result.noul <= 1.0

    def test_rate_returns_typed_score_result(self, server_url):
        with JevClient(server_url) as client:
            result = client.rate(STATE, LEVELS, "请求复杂程度？")
        assert isinstance(result, ScoreResult)
        assert 0.0 <= result.score <= float(len(LEVELS) - 1)
        assert set(result.legend.values()) == set(LEVELS)
        assert result.mode_level in LEVELS

    def test_system_one_multi_question(self, server_url):
        from jev.kernel.cerebellum.base import ChoiceQuestion, ScoreQuestion

        with JevClient(server_url) as client:
            response = client.system_one(
                STATE,
                {
                    "q1": ChoiceQuestion(choices=CHOICES, instructions="路径？"),
                    "q2": ScoreQuestion(criteria=LEVELS, instructions="复杂度？"),
                },
            )
        assert set(response.answers) == {"q1", "q2"}
        assert response.input_tokens >= 0

    def test_chat_returns_typed_chat_result_with_cascade_meta(self, server_url):
        with JevClient(server_url) as client:
            result = client.chat(
                [{"role": "user", "content": "这个请求是决策类的吗"}], max_tokens=32
            )
        assert isinstance(result, ChatResult)
        # direct 路由：模板合成内容（无需大脑）
        assert result.route == "direct"
        assert result.decisions == 4
        assert result.degraded is False
        assert result.content.strip()


class TestJevClientAsync:
    def test_async_decide_judge_rate_chat(self, server_url):
        async def _run() -> tuple:
            async with JevClient(server_url) as client:
                choice = await client.adecide(STATE, CHOICES, "选择应答路径")
                noul = await client.ajudge(STATE, "该陈述是否为真？")
                score = await client.arate(STATE, LEVELS, "复杂程度？")
                chat = await client.achat(
                    [{"role": "user", "content": "决策类请求"}], max_tokens=32
                )
            return choice, noul, score, chat

        choice, noul, score, chat = asyncio.run(_run())
        assert isinstance(choice, ChoiceResult) and choice.choice in CHOICES
        assert isinstance(noul, NoulResult) and 0.0 <= noul.noul <= 1.0
        assert isinstance(score, ScoreResult) and score.mode_level in LEVELS
        assert isinstance(chat, ChatResult) and chat.route == "direct"


class TestJevClientErrors:
    def test_dead_port_raises_typed_unavailable(self):
        port = _free_port()  # 绑定后立即释放：真实连接拒绝
        with pytest.raises(JevUnavailableError):
            with JevClient(f"http://127.0.0.1:{port}", timeout_s=2.0) as client:
                client.decide(STATE, CHOICES, "选择应答路径")

    def test_server_side_validation_maps_to_protocol_error(self, server_url):
        with pytest.raises(JevProtocolError):
            with JevClient(server_url) as client:
                # 空指令 → 服务端真实 400（TypeSafe 线协议校验）
                client.judge(STATE, "")


class TestProgrammingApi:
    def test_register_point_equivalent_to_toml_and_usable(
        self, cerebellum, model_path, tokenizer_path, config_path
    ):
        """编程注册与 TOML 等价：同字段落注册表，注册后立即参与真实决策。"""
        registry = _direct_registry()
        kernel = Kernel(
            cerebellum=cerebellum, registry=registry, breaker=Breaker()
        )
        custom = DecisionPoint(
            id="my_gate",
            stage=Stage.PRE,
            question="自定义门控: allow or block?",
            tau=0.7,
            fallback_action="block",
            question_type="choice",
            options=("allow", "block"),
            mode=Mode.SHADOW,
        )
        register_point(kernel, custom)
        # 注册表中等价存在（frozen dataclass 全等比较）
        assert kernel.registry.get("my_gate") == custom

        # 注册后立即参与真实决策（真前向，shadow 模式照常出答案）
        from jev.kernel.decisions.framework import to_question

        question = to_question(kernel.registry.get("my_gate"))
        response = kernel.system_one("自定义状态", {"my_gate": question})
        assert response.answers["my_gate"].choice in {"allow", "block"}
