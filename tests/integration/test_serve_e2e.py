"""T015 IT：`jev serve` 端到端——真实子进程拉起/health/双端点真实服务/退出无残留。

FR3 + FR9：serve 按需拉起 llama-server（cudart PATH 由 Manager 注入），
就绪探针通过后 brain=true；/v1/systemone 与 /v1/chat/completions 真实
服务；进程树终结后端口全释放（无残留）。
"""

import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytestmark = [pytest.mark.assets, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _post_json(url: str, payload: dict, timeout: float) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


class TestJevServeEndToEnd:
    def test_serve_spawn_health_dual_endpoints_and_clean_exit(self, tmp_path):
        server_port = _free_port()
        brain_port = _free_port()
        models = REPO_ROOT / "models"

        # decision-points 与配置同目录（from_config 默认解析约定）
        (tmp_path / "decision-points.toml").write_bytes(
            (REPO_ROOT / "configs" / "decision-points.toml").read_bytes()
        )
        config = tmp_path / "serve.toml"
        config.write_text(
            f"""
[server]
host = "127.0.0.1"
port = {server_port}

[cerebellum]
default_backend = "laya_onnx"

[cerebellum.laya_onnx]
model_path = "{(models / 'laya' / 'laya.onnx').as_posix()}"
calibration_path = "{(models / 'laya' / 'calibration.json').as_posix()}"

[cerebrum]
llama_server_bin = "{(models / 'llama-server' / 'bin' / 'llama-server.exe').as_posix()}"
gguf_path = "{(models / 'qwen' / 'qwen2.5-1.5b-instruct-q4_k_m.gguf').as_posix()}"
port = {brain_port}
ctx_size = 2048
restart_max = 1

[breaker]
failure_threshold = 3
window_s = 60
cooldown_s = 30
cooldown_max_s = 120

[ledger]
dir = "{(tmp_path / 'ledger').as_posix()}"
""",
            encoding="utf-8",
        )

        proc = subprocess.Popen(
            [sys.executable, "-m", "jev.cli.main", "serve", "--config", str(config)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(REPO_ROOT),
        )
        try:
            # 就绪探针：/health 200 且五字段契约齐备、cerebrum=true
            # （llama-server CUDA 加载约 15~25s）
            deadline = time.monotonic() + 150
            health = None
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    output = proc.stdout.read() if proc.stdout else ""
                    pytest.fail(f"jev serve 提前退出:\n{output[-800:]}")
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{server_port}/health", timeout=2
                    ) as response:
                        health = json.loads(response.read().decode("utf-8"))
                except (OSError, urllib.error.URLError):
                    health = None
                if health and health.get("cerebrum") is True:
                    break
                time.sleep(1.0)
            assert health is not None
            assert health["status"] == "ok"
            assert set(health) == {
                "status", "cerebellum", "cerebrum", "breaker", "uptime",
            }
            assert health["cerebellum"] is True
            assert health["cerebrum"] is True
            assert health["breaker"] == "closed"
            assert health["uptime"] >= 0

            # 小脑决策端点：真实 laya 前向
            decision = _post_json(
                f"http://127.0.0.1:{server_port}/v1/systemone",
                {
                    "model": "jev",
                    "state": "用一句话介绍你自己",
                    "questions": {
                        "intent": {
                            "type": "choice",
                            "instructions": "该请求的意图属于哪类？",
                            "criteria": {
                                "decision": "决策类", "simple": "简单生成", "complex": "复杂推理",
                            },
                        }
                    },
                },
                timeout=30,
            )
            assert decision["answers"]["intent"]["choice"] in {
                "decision", "simple", "complex",
            }

            # chat 级联端点：真大脑生成（real llama-server）
            chat = _post_json(
                f"http://127.0.0.1:{server_port}/v1/chat/completions",
                {
                    "messages": [{"role": "user", "content": "用一句话介绍你自己"}],
                    "max_tokens": 32,
                },
                timeout=180,
            )
            content = chat["choices"][0]["message"]["content"]
            assert isinstance(content, str) and content.strip()
        finally:
            # 进程树终结（serve + llama-server 子进程）
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, check=False,
            )
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                pass

        # 退出无残留：服务端口与大脑端口均释放
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and (
            _port_open(server_port) or _port_open(brain_port)
        ):
            time.sleep(0.5)
        assert not _port_open(server_port), "serve 退出后服务端口仍被占用"
        assert not _port_open(brain_port), "serve 退出后大脑端口仍被占用（llama-server 残留）"
