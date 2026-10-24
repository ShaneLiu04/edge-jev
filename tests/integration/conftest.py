"""integration 共享 fixture：真实资产（jev fetch --all 就位后运行，assets 标记）。

- real_kernel：configs/jev.toml 单源装配的真实 Laya bundle（1.6GB 前向，CPU EP）
- brain_manager：真实 llama-server.exe 子进程（CUDA，cudart DLL 目录注入 PATH）
- brain_client：大脑 OpenAI 兼容客户端

llama-server bin 不自带 cudart（资产布局分 bin/ 与 cudart/ 两目录）——
PATH 注入在此完成；serve 编排（T015）将归纳同款环境处理。
"""

import os
import socket
from pathlib import Path

import pytest

from jev.kernel.cerebrum.llama_server import LlamaServerClient, LlamaServerManager
from jev.kernel.kernel import Kernel

REPO_ROOT = Path(__file__).resolve().parents[2]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def spawn_brain(*, ctx_size: int = 2048) -> LlamaServerManager:
    """拉起一独享真实 llama-server 实例（调用方负责 stop）。"""
    cudart_dir = REPO_ROOT / "models" / "llama-server" / "cudart"
    assert cudart_dir.is_dir(), f"cudart 资产目录缺失: {cudart_dir}（先 jev fetch --all）"
    os.environ["PATH"] = str(cudart_dir) + os.pathsep + os.environ["PATH"]

    manager = LlamaServerManager(
        llama_server_bin=REPO_ROOT / "models" / "llama-server" / "bin" / "llama-server.exe",
        gguf_path=REPO_ROOT / "models" / "qwen" / "qwen2.5-1.5b-instruct-q4_k_m.gguf",
        port=free_port(),
        ctx_size=ctx_size,
        probe_timeout_s=120.0,
        restart_backoff_s=(2.0, 4.0, 8.0),
    )
    manager.start()
    return manager


@pytest.fixture(scope="session")
def real_kernel() -> Kernel:
    return Kernel.from_config(REPO_ROOT / "configs" / "jev.toml")


@pytest.fixture(scope="session")
def brain_manager():
    manager = spawn_brain()
    yield manager
    manager.stop()


@pytest.fixture()
def own_brain_manager():
    """独享短生命周期实例（生命周期测试专用；本测试内 stop，teardown 幂等兜底）。"""
    manager = spawn_brain()
    yield manager
    manager.stop()


@pytest.fixture(scope="session")
def brain_client(brain_manager) -> LlamaServerClient:
    return LlamaServerClient(brain_manager.base_url, timeout_s=120.0)
