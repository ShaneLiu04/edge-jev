"""T005：LlamaServerManager 测试——真实 python.exe 子进程作"假 llama-server"，禁 mock。

约束 C1：llama-server 真实推理属 assets 集成测试；本文件测子进程管理器生命周期——
拉起/健康探测/崩溃自动重启/优雅退出。被管对象是真实 OS 进程：由 sys.executable
以 `python -m fake_llama_server` 拉起（与真实 llama-server 同样的命令行形状
[bin, -m, gguf, --host, ..., --port, ..., -c, ...]），且是直接子进程——
terminate/kill 直接命中真实工作进程，无 .bat 中间层导致的孤儿进程假象；
taskkill/端口监听/进程退出均为真实 OS 语义。
"""

import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from jev.errors import BrainStartupError, JevProtocolError
from jev.kernel.cerebrum.llama_server import LlamaServerManager

# 假 llama-server：真实 python 子进程脚本（写入 tmp_path 的包模块），行为由
# 命令行参数驱动：默认监听 http 并服务 /health；--pidfile 记录自身 pid；
# --attempts-file 每次启动追加一行（验证重启链真实执行）；--fail-fast N 立即
# 向 stderr 写诊断标记并以码 N 退出（不监听端口）。
FAKE_SERVER_MAIN = r'''import argparse
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8388)
    parser.add_argument("-c", dest="ctx_size", type=int, default=4096)
    parser.add_argument("--pidfile")
    parser.add_argument("--attempts-file")
    parser.add_argument("--fail-fast", type=int, default=0)
    args, _unknown = parser.parse_known_args()

    if args.attempts_file:
        with Path(args.attempts_file).open("a", encoding="utf-8") as fh:
            fh.write(f"{os.getpid()}\n")

    if args.fail_fast:
        print(
            f"fake-llama-fail-fast: model load failed (exit {args.fail_fast})",
            file=sys.stderr,
            flush=True,
        )
        sys.exit(args.fail_fast)

    class HealthHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def _send_json(self, status: int, payload: str) -> None:
            body = payload.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/health":
                self._send_json(200, '{"status": "ok"}')
            else:
                self._send_json(404, '{"error": "not found"}')

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            if self.path == "/v1/chat/completions":
                # EX-12：chat 恢复断言需要真 OpenAI 形状响应
                self._send_json(
                    200,
                    '{"id":"chatcmpl-fake","object":"chat.completion","model":"local",'
                    '"choices":[{"index":0,"message":{"role":"assistant",'
                    '"content":"fake brain answer"},"finish_reason":"stop"}],'
                    '"usage":{"prompt_tokens":5,"completion_tokens":3,"total_tokens":8}}',
                )
            else:
                self._send_json(404, '{"error": "not found"}')

    server = ThreadingHTTPServer((args.host, args.port), HealthHandler)
    if args.pidfile:
        Path(args.pidfile).write_text(str(os.getpid()), encoding="utf-8")
    server.serve_forever()


if __name__ == "__main__":
    main()
'''


def _install_fake_server(root: Path) -> None:
    """写入真实 .py 包模块，供 sys.executable -m 作为子进程执行。"""
    pkg = root / "fake_llama_server"
    pkg.mkdir(exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__main__.py").write_text(FAKE_SERVER_MAIN, encoding="utf-8")


def _real_python() -> str:
    """真实解释器路径：uv venv 的 python.exe 是 trampoline（Popen 返回跳板 pid，
    真实解释器为其子进程——terminate 只杀跳板会遗留孤儿进程继续占端口）。
    sys._base_executable 指向真实基础解释器，保证被管对象是直接子进程。
    """
    return sys._base_executable or sys.executable


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _assert_port_refused(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        assert sock.connect_ex(("127.0.0.1", port)) != 0, f"端口 {port} 仍有进程监听"


def _health_ok(base_url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base_url}/health", timeout=2.0) as resp:
            return 200 <= resp.status < 300
    except OSError:
        return False


def _wait_health(base_url: str, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _health_ok(base_url):
            return True
        time.sleep(0.1)
    return False


def _wait_not_running(manager: LlamaServerManager, timeout_s: float = 5.0) -> None:
    """taskkill /F 为异步语义：等待管理器观测到进程退出（消除竞态假阳）。"""
    deadline = time.monotonic() + timeout_s
    while manager.is_running() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not manager.is_running(), "taskkill 后进程仍存活（超时 5s）"


@pytest.fixture()
def llama_factory(tmp_path, monkeypatch):
    """产出管理真实子进程的 LlamaServerManager；teardown 统一 stop 保证无残留。

    假 llama-server 经 `sys.executable -m fake_llama_server` 拉起——命令行形状
    与真实 llama-server 一致；PYTHONPATH 与 cwd 双通道保证子进程可解析模块，
    兼容实现侧 Popen 是否显式传 env/cwd。
    """
    _install_fake_server(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    created: list[LlamaServerManager] = []

    def make(port: int, **overrides) -> LlamaServerManager:
        kwargs: dict = dict(
            llama_server_bin=Path(_real_python()),
            gguf_path=Path("fake_llama_server"),
            host="127.0.0.1",
            port=port,
            probe_timeout_s=5.0,
            restart_backoff_s=(0.05, 0.1, 0.15),
        )
        kwargs.update(overrides)
        manager = LlamaServerManager(**kwargs)
        created.append(manager)
        return manager

    yield make

    for manager in created:
        manager.stop()


# ---------------------------------------------------------------------------
# start / stop / 上下文管理器：真实子进程生命周期
# ---------------------------------------------------------------------------


def test_start_spawns_real_subprocess_and_probes_health(llama_factory):
    port = _free_port()
    manager = llama_factory(port)

    manager.start()

    assert manager.is_running()
    assert manager.pid > 0
    assert manager.base_url == f"http://127.0.0.1:{port}"
    assert _health_ok(manager.base_url)


def test_stop_terminates_process_and_releases_port(llama_factory):
    port = _free_port()
    manager = llama_factory(port)
    manager.start()
    assert manager.is_running()

    manager.stop()

    assert not manager.is_running()
    _assert_port_refused(port)


def test_context_manager_exit_leaves_no_process(llama_factory):
    port = _free_port()
    manager = llama_factory(port)

    with manager:
        manager.start()
        assert manager.is_running()

    assert not manager.is_running()
    _assert_port_refused(port)


def test_stop_on_never_started_manager_is_idempotent(llama_factory):
    port = _free_port()
    manager = llama_factory(port)

    manager.stop()
    manager.stop()

    assert not manager.is_running()


# ---------------------------------------------------------------------------
# 启动失败 / 重复启动
# ---------------------------------------------------------------------------


def test_start_failure_raises_brain_startup_with_diagnostics(llama_factory):
    port = _free_port()
    # 脚本立即 exit(3) 且不监听端口 → 探测超时，须携带退出码或 stderr 诊断
    manager = llama_factory(port, probe_timeout_s=2.0, extra_args=["--fail-fast", "3"])

    with pytest.raises(BrainStartupError) as excinfo:
        manager.start()

    message = str(excinfo.value)
    assert "3" in message or "fake-llama-fail-fast" in message
    assert not manager.is_running()


def test_double_start_raises_protocol_error(llama_factory):
    port = _free_port()
    manager = llama_factory(port)
    manager.start()

    with pytest.raises(JevProtocolError):
        manager.start()


# ---------------------------------------------------------------------------
# 崩溃自动重启 / 重启耗尽
# ---------------------------------------------------------------------------


def test_ensure_running_restarts_after_external_kill(llama_factory, tmp_path):
    port = _free_port()
    pidfile = tmp_path / "server.pid"
    manager = llama_factory(port, extra_args=["--pidfile", str(pidfile)])
    manager.start()
    old_pid = manager.pid
    assert pidfile.read_text(encoding="utf-8") == str(old_pid)

    subprocess.run(["taskkill", "/F", "/PID", str(old_pid)], check=True, capture_output=True)
    _wait_not_running(manager)

    manager.ensure_running()

    assert manager.is_running()
    assert manager.pid != old_pid
    assert _wait_health(manager.base_url)
    assert pidfile.read_text(encoding="utf-8") == str(manager.pid)


def test_ensure_running_raises_brain_startup_after_repeated_failures(llama_factory, tmp_path):
    port = _free_port()
    attempts_file = tmp_path / "attempts.txt"
    manager = llama_factory(
        port,
        probe_timeout_s=1.0,
        restart_max=2,
        restart_backoff_s=(0.05, 0.1),
        extra_args=["--fail-fast", "1", "--attempts-file", str(attempts_file)],
    )

    with pytest.raises(BrainStartupError):
        manager.ensure_running()

    attempts = attempts_file.read_text(encoding="utf-8").splitlines()
    assert len(attempts) >= 2, "重启链必须真实执行多次拉起而非首次失败即放弃"
    assert not manager.is_running()


# ---------------------------------------------------------------------------
# EX-12（流程图 E Fused→Starting 恢复边）：重启耗尽熔断 → serve 重启恢复 → chat 200
# ---------------------------------------------------------------------------


def test_fused_manager_recovers_after_serve_restart(
    llama_factory, tmp_path, model_path, tokenizer_path, config_path
):
    """大脑熔断进入 Fused 后重启 serve：健康探测通过、chat 恢复 200。"""
    from fastapi.testclient import TestClient

    from jev.kernel.cascade.breaker import Breaker
    from jev.kernel.cerebellum.laya_onnx import LayaOnnxBackend
    from jev.kernel.decisions.framework import DecisionPoint, DecisionRegistry, Stage
    from jev.kernel.kernel import Kernel
    from jev.server.app import build_serve_app

    port = _free_port()

    # 1) Fused：fail-fast + 重启耗尽 → BrainStartupError（大脑侧熔断，chat 503）
    attempts_file = tmp_path / "fused-attempts.txt"
    fused = llama_factory(
        port,
        probe_timeout_s=1.0,
        restart_max=1,
        restart_backoff_s=(0.05,),
        extra_args=["--fail-fast", "1", "--attempts-file", str(attempts_file)],
    )
    with pytest.raises(BrainStartupError):
        fused.ensure_running()
    assert not fused.is_running()

    # 2) serve 重启（Fused→Starting 恢复边）：新管理器拉起同端口真实进程
    recovered = llama_factory(port, probe_timeout_s=5.0)
    recovered.start()
    assert recovered.is_running()
    assert _wait_health(recovered.base_url)

    # 3) chat 恢复 200：full 路由经真实子进程大脑生成
    registry = DecisionRegistry()
    registry.register(
        DecisionPoint(
            id="intent",
            stage=Stage.PRE,
            question="intent: decision or complex?",
            tau=0.7,
            fallback_action="complex",
            question_type="choice",
            options=("decision", "complex"),
        )
    )
    kernel = Kernel(
        cerebellum=LayaOnnxBackend(
            model_path=model_path,
            tokenizer_path=tokenizer_path,
            config_path=config_path,
        ),
        registry=registry,
        breaker=Breaker(failure_threshold=3, window_s=60.0, cooldown_s=30.0),
        brain=recovered,
    )
    try:
        client = TestClient(build_serve_app(kernel))
        resp = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hello"}], "max_tokens": 16},
        )
        assert resp.status_code == 200
        assert resp.headers["x-jev-route"] == "full"
        assert (
            resp.json()["choices"][0]["message"]["content"] == "fake brain answer"
        )
        health = client.get("/health").json()
        assert health["cerebrum"] is True
        assert health["status"] == "ok"
    finally:
        kernel.close()
