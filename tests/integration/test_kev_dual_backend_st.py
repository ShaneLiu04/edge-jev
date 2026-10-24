"""ST-005（FR2 验收 1）：Laya 与 kev 双后端结构一致（真实双系统）。

真实链路：真 Laya ONNX（进程内）+ 真 kev.serve 子进程（kev-0.8B，本仓
models/kev/repo 独立 venv，Python 3.12）。同一 (state, questions) 分别经
两后端推理，断言结构一致（Choice/Noul/Score 三型答案合法、选项集合对齐）；
数值允许不同（不同模型），结构必须一致。

ST 真实对端发现并修复：kev.serve 无 /health 路由（TypeSafe 面只有
/v1/systemone 与 /v1/models），KevHttpBackend.warmup 改探 /v1/models。
"""

import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import httpx
import pytest

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

pytestmark = [pytest.mark.assets, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]
KEV_REPO = REPO_ROOT / "models" / "kev" / "repo"
KEV_RUN = "jaredpalmer/kev-0.8b"

STATE = "用户希望在内网环境离线部署一个本地推理服务，关注延迟与可维护性。"
QUESTIONS = {
    "q_path": ChoiceQuestion(
        choices={"direct": "小脑直答", "cascade": "级联大脑"}, instructions="选择应答路径"
    ),
    "q_ctx": NoulQuestion(instructions="判断是否需要补充上下文"),
    "q_risk": ScoreQuestion(criteria=["low", "mid", "high"], instructions="给出风险等级"),
}


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def kev_server():
    """拉起真实 kev.serve（模型加载需分钟级；HF 走内网镜像，断 xet）。"""
    assert KEV_REPO.is_dir(), f"kev 仓缺失: {KEV_REPO}（先 jev fetch --all）"
    port = _free_port()
    env = dict(os.environ)
    env.update(
        HF_ENDPOINT="http://mirrors.tools.huawei.com/huggingface",
        HF_HUB_DISABLE_XET="1",
    )
    out = Path(os.environ["TEMP"]) / "opencode" / "kev-st-serve.log"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("wb") as log:
        proc = subprocess.Popen(
            [
                "uv", "run", "--no-sync", "--extra", "serve",
                "python", "-m", "kev.serve", "--run", KEV_RUN, "--port", str(port),
            ],
            cwd=str(KEV_REPO),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 300.0
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                tail = out.read_text(encoding="utf-8", errors="replace")[-2000:]
                raise RuntimeError(f"kev.serve 提前退出（code={proc.returncode}）:\n{tail}")
            try:
                with urllib.request.urlopen(f"{base_url}/v1/models", timeout=5) as resp:
                    if resp.status == 200:
                        break
            except (urllib.error.URLError, TimeoutError, OSError):
                time.sleep(3.0)
        else:
            raise RuntimeError("kev.serve 5 分钟未就绪")
        yield base_url
    finally:
        if proc.poll() is None:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                check=False,
                capture_output=True,
            )
            try:
                proc.wait(timeout=15.0)
            except subprocess.TimeoutExpired:
                pass


def test_real_kev_backend_warmup_passes(kev_server):
    """warmup 探测真实 kev.serve 的 /v1/models（/health 缺失缺陷的端到端回归）。"""
    backend = KevHttpBackend(serve_url=kev_server, timeout_s=30.0)
    backend.warmup()
    backend.close()


def test_laya_and_kev_backends_answer_structurally_consistent(
    real_kernel, kev_server
):
    laya = real_kernel.cerebellum
    kev = KevHttpBackend(serve_url=kev_server, model="kev-0.8b", timeout_s=60.0)
    try:
        laya_resp = laya.system_one(STATE, QUESTIONS)
        kev_resp = kev.system_one(STATE, QUESTIONS)
    finally:
        kev.close()

    # 同一问题集合、同型答案、合法结构（两后端共同的 CerebellumBackend 契约）
    assert set(laya_resp.answers) == set(kev_resp.answers) == set(QUESTIONS)
    for backend_resp in (laya_resp, kev_resp):
        choice = backend_resp.answers["q_path"]
        assert isinstance(choice, ChoiceAnswer)
        assert choice.type == "choice"
        assert set(choice.probabilities) == {"direct", "cascade"}
        assert sum(choice.probabilities.values()) == pytest.approx(1.0, abs=1e-3)
        assert choice.choice in choice.probabilities
        assert 0.0 <= choice.confidence <= 1.0

        noul = backend_resp.answers["q_ctx"]
        assert isinstance(noul, NoulAnswer)
        assert noul.type == "noul"
        assert 0.0 <= noul.noul <= 1.0
        assert 0.0 <= noul.confidence <= 1.0

        score = backend_resp.answers["q_risk"]
        assert isinstance(score, ScoreAnswer)
        assert score.type == "score"
        assert set(score.probabilities) == {"0", "1", "2"}
        assert sum(score.probabilities.values()) == pytest.approx(1.0, abs=1e-3)
        assert 0.0 <= score.confidence <= 1.0

        assert backend_resp.input_tokens > 0


def test_real_kev_backend_serves_via_httpx_roundtrip(kev_server):
    """kev.serve 原生 /v1/systemone 线协议可被标准 HTTP 客户端消费（TypeSafe criteria 格式）。"""
    payload = encode_request("kev-0.8b", STATE, QUESTIONS)
    response = httpx.post(f"{kev_server}/v1/systemone", json=payload, timeout=60.0)
    assert response.status_code == 200
    body = response.json()
    assert body["answers"]["q_path"]["type"] == "choice"
    assert set(body["answers"]["q_path"]["probabilities"]) == {"direct", "cascade"}
    assert body["usage"]["input_tokens"] > 0
    assert "x-typesafe-request-id" in response.headers
