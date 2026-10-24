"""T017：MCP Server——jev_decide/jev_judge/jev_rate 三 tools（FR11，IT-07/IT-12）。

约束 C1：禁 mock。后端为真实 uvicorn 线程服务（微型契约 bundle 小脑）；
MCP 协议经真实 in-proc 会话（memory streams + ClientSession 初始化握手 +
list_tools/call_tool 真往返）；不可达路径用真实死端口（连接拒绝）。
"""

import socket
import threading
import time

import anyio
import pytest
from mcp import ClientSession
from mcp.shared.memory import create_client_server_memory_streams
from typer.testing import CliRunner

from jev.cli.main import app as cli_app
from jev.kernel.cerebellum.laya_onnx import LayaOnnxBackend
from jev.mcp_server import create_mcp_server
from jev.sdk import JevClient
from jev.server.systemone import create_app

STATE = "用一句话介绍你自己"
CHOICES = {"direct": "小脑直答", "cascade": "级联大脑"}
LEVELS = ["low", "mid", "high"]

runner = CliRunner()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture()
def server_url(model_path, tokenizer_path, config_path):
    """真实 uvicorn 线程托管 /v1/systemone（微型契约 bundle 小脑）。"""
    cerebellum = LayaOnnxBackend(
        model_path=model_path, tokenizer_path=tokenizer_path, config_path=config_path
    )
    http_app = create_app(cerebellum)
    port = _free_port()
    config = uvicorn_config(http_app, port)
    server = uvicorn_server(config)
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


def uvicorn_config(http_app, port):
    import uvicorn

    return uvicorn.Config(http_app, host="127.0.0.1", port=port, log_level="warning")


def uvicorn_server(config):
    import uvicorn

    return uvicorn.Server(config)


async def _session_roundtrip(mcp_server, calls):
    """In-proc MCP 会话：初始化握手 → list_tools → 逐 call_tool 真往返。"""
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        lowlevel = mcp_server._lowlevel_server
        async with anyio.create_task_group() as tg:
            tg.start_soon(
                lowlevel.run,
                server_streams[0],
                server_streams[1],
                lowlevel.create_initialization_options(),
            )
            async with ClientSession(*client_streams) as session:
                await session.initialize()
                tools = await session.list_tools()
                results = []
                for name, arguments in calls:
                    results.append(await session.call_tool(name, arguments))
            tg.cancel_scope.cancel()
    return tools, results


def _text_of(result) -> str:
    parts = [getattr(block, "text", "") for block in result.content]
    return "\n".join(parts)


class TestMcpTools:
    def test_exposes_exactly_three_typed_tools(self, server_url):
        mcp_server = create_mcp_server(JevClient(server_url))
        tools, _ = anyio.run(
            _session_roundtrip, mcp_server, [("jev_decide", None)]
        )
        names = {tool.name for tool in tools.tools}
        assert names == {"jev_decide", "jev_judge", "jev_rate"}
        for tool in tools.tools:
            assert tool.input_schema  # 参数 schema 由类型标注生成（含 state/choices 等）

    def test_decide_tool_returns_structured_decision(self, server_url):
        mcp_server = create_mcp_server(JevClient(server_url))
        _, results = anyio.run(
            _session_roundtrip,
            mcp_server,
            [
                (
                    "jev_decide",
                    {"state": STATE, "choices": CHOICES, "instructions": "选择应答路径"},
                )
            ],
        )
        (result,) = results
        assert not result.is_error
        data = result.structured_content
        assert data["choice"] in CHOICES
        assert 0.0 <= data["confidence"] <= 1.0
        assert set(data["probabilities"]) == set(CHOICES)

    def test_judge_and_rate_tools_return_structured_results(self, server_url):
        mcp_server = create_mcp_server(JevClient(server_url))
        _, results = anyio.run(
            _session_roundtrip,
            mcp_server,
            [
                ("jev_judge", {"state": STATE, "instructions": "该陈述是否为真？"}),
                (
                    "jev_rate",
                    {"state": STATE, "criteria": LEVELS, "instructions": "请求复杂程度？"},
                ),
            ],
        )
        judge, rate = results
        assert not judge.is_error
        assert 0.0 <= judge.structured_content["noul"] <= 1.0
        assert not rate.is_error
        assert rate.structured_content["mode_level"] in LEVELS
        assert set(rate.structured_content["legend"].values()) == set(LEVELS)

    def test_unreachable_service_maps_to_typed_error(self):
        """IT-12：服务不可达 → JevUnavailableError 类型化异常（不裸抛连接错误）。"""
        port = _free_port()  # 绑定后立即释放：真实连接拒绝
        mcp_server = create_mcp_server(JevClient(f"http://127.0.0.1:{port}", timeout_s=2.0))
        _, results = anyio.run(
            _session_roundtrip,
            mcp_server,
            [("jev_decide", {"state": STATE, "choices": CHOICES, "instructions": "路径？"})],
        )
        (result,) = results
        assert result.is_error
        message = _text_of(result)
        assert "JevUnavailableError" in message or "不可达" in message


class TestMcpCli:
    def test_mcp_help_exits_zero_and_shows_url_option(self):
        result = runner.invoke(cli_app, ["mcp", "--help"])
        assert result.exit_code == 0
        assert "--url" in result.output
