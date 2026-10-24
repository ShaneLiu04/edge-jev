"""FR1-2（review 补齐）：/v1/chat/completions 可被 openai 官方 SDK 直接消费。

真实 openai SDK 经真实 uvicorn HTTP socket 访问（test_sdk 同款模式）；
direct 路由注册表 + 微型真实契约 bundle，SDK 解析出标准 ChatCompletion。
"""

import socket
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI
from openai import OpenAI

from jev.kernel.cascade.engine import CascadeEngine
from jev.kernel.cerebellum.laya_onnx import LayaOnnxBackend
from jev.kernel.decisions.framework import DecisionPoint, DecisionRegistry, Stage
from jev.server.chat import create_chat_router


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _direct_registry() -> DecisionRegistry:
    registry = DecisionRegistry()
    registry.register(
        DecisionPoint(
            id="intent",
            stage=Stage.PRE,
            question="intent: simple or decision?",
            tau=0.7,
            fallback_action="decision",
            question_type="choice",
            options=("simple", "decision"),
        )
    )
    return registry


@pytest.fixture()
def server_url(model_path, tokenizer_path, config_path):
    """真实 uvicorn 线程托管 chat（direct 路由，无需大脑）。"""
    registry = _direct_registry()
    app = FastAPI()
    app.include_router(
        create_chat_router(
            LayaOnnxBackend(
                model_path=model_path,
                tokenizer_path=tokenizer_path,
                config_path=config_path,
            ),
            None,
            registry,
            engine=CascadeEngine(registry),
        )
    )
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.5)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.1)
    else:
        pytest.fail("uvicorn 测试服务器未就绪")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_openai_sdk_consumes_chat_completions(server_url):
    sdk = OpenAI(
        base_url=f"{server_url}/v1",
        api_key="test-key",  # 本地服务不校鉴权，占位即可
    )
    completion = sdk.chat.completions.create(
        model="local",
        messages=[{"role": "user", "content": "hello world jev"}],
        max_tokens=64,
    )
    # SDK 解析为标准 ChatCompletion（OpenAI 兼容契约的最终裁判）
    assert completion.object == "chat.completion"
    content = completion.choices[0].message.content
    assert isinstance(content, str) and content.startswith("【Jev 小脑直答")
    assert completion.usage is not None
    assert completion.usage.total_tokens == 0  # direct 路由不产 token
