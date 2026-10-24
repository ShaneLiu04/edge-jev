"""ST-009（FR3 验收 2）：真实 llama-server 强杀自动恢复。

EX-12 已在真子进程（假 llama-server 二进制形状）验证恢复机制；本用例在 ST
对真实 llama-server.exe + 真实 GGUF 重复该场景：taskkill /F 强杀 →
ensure_running 自动重启 → 真实 chat 200。
"""

import subprocess
import time

import pytest

from jev.kernel.cerebrum.llama_server import LlamaServerClient

pytestmark = [pytest.mark.assets, pytest.mark.slow]

CHAT_MESSAGES = [{"role": "user", "content": "用一句话介绍你自己"}]


def test_real_brain_restarts_after_external_kill(own_brain_manager):
    manager = own_brain_manager
    client = LlamaServerClient(manager.base_url, timeout_s=120.0)
    assert client.health() is True
    old_pid = manager.pid

    subprocess.run(
        ["taskkill", "/F", "/PID", str(manager.pid)],
        check=True,
        capture_output=True,
    )
    # taskkill /F 为异步语义：等待管理器观测到退出，否则 ensure_running
    # 误判"仍存活"直接返回（进程随后才真正死亡 → 连接被拒）。
    deadline = time.monotonic() + 10.0
    while manager.is_running() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not manager.is_running(), "taskkill 后进程仍存活（超时 10s）"

    manager.ensure_running()

    assert manager.is_running()
    assert manager.pid != old_pid
    # 重启后新建客户端：强杀会使既有 httpx 连接池留下陈旧 socket（复用即
    # 10054 → JevUnavailableError，随后池自动淘汰、下一请求自愈——请求级
    # 失败而非服务级故障）；此处验证恢复后的服务真实可用。
    fresh = LlamaServerClient(manager.base_url, timeout_s=120.0)
    response = fresh.chat(CHAT_MESSAGES, max_tokens=32)
    content = response["choices"][0]["message"]["content"]
    assert isinstance(content, str) and content.strip()
