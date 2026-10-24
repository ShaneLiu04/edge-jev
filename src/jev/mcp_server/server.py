"""jev MCP Server（FR11）：decide/judge/rate 三 tools，stdio transport。

薄壳（C 架构：薄壳 → SDK JevClient → jev serve → Kernel），不自持
小脑/大脑进程；MCP 生态客户端（Claude Code/Cursor 等）经 stdio 消费。
工具直接返回 SDK 类型化 Result（mcp 据此发布结构化 output schema）。
异常契约（design.md §4.3.3）：服务不可达 → JevUnavailableError 类型化
异常（IT-12），不裸抛连接错误。
"""

import functools

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

import jev
from jev.errors import JevError
from jev.sdk import JevClient
from jev.sdk.results import ChoiceResult, NoulResult, ScoreResult


def _typed(fn):
    """JevError 族 → ToolError（类型名入消息）：MCP 客户端可见的类型化异常。"""

    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except JevError as exc:
            raise ToolError(f"{type(exc).__name__}: {exc}") from exc

    return wrapped


def create_mcp_server(client: JevClient) -> MCPServer:
    """构建 MCP server：三 tools 代理 JevClient（decide/judge/rate）。"""
    server = MCPServer(name="jev", version=jev.__version__)

    def jev_decide(state: str, choices: dict[str, str | None], instructions: str) -> ChoiceResult:
        """小脑结构化决策：给定状态与选项，返回最优选项及置信分布。"""
        return client.decide(state, choices, instructions)

    def jev_judge(state: str, instructions: str, criteria: dict | None = None) -> NoulResult:
        """小脑真值判断：对陈述做二值判断，返回 Noul 值与置信度。"""
        return client.judge(state, instructions, criteria)

    def jev_rate(state: str, criteria: list[str], instructions: str) -> ScoreResult:
        """小脑等级评定：按等级量表打分，返回分数/众数等级与分布。"""
        return client.rate(state, criteria, instructions)

    server.add_tool(
        _typed(jev_decide),
        name="jev_decide",
        title="Jev 决策",
        description="快思考小脑结构化决策：给定状态与候选选项，返回最优选项、置信度与全选项概率分布。",
    )
    server.add_tool(
        _typed(jev_judge),
        name="jev_judge",
        title="Jev 判断",
        description="快思考小脑真值判断：对给定陈述做二值判断，返回 Noul 真值与置信度。",
    )
    server.add_tool(
        _typed(jev_rate),
        name="jev_rate",
        title="Jev 评定",
        description="快思考小脑等级评定：按等级量表对状态打分，返回期望分数、众数等级与等级概率分布。",
    )
    return server


__all__ = ["create_mcp_server"]
