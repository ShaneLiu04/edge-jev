"""T015/review 修订：serve 应用装配——双协议端点 + /health（真实 Kernel.from_config）。

依赖方向修订（review C2）：应用组装移至 jev.server.app（server → kernel），
Kernel 不再反向依赖 server；/health 契约补齐五字段
{status, cerebellum, cerebrum, breaker, uptime}（design §4.3.1）。
"""

from pathlib import Path

from fastapi.testclient import TestClient

from jev.kernel.kernel import Kernel
from jev.server.app import build_serve_app

REPO_ROOT = Path(__file__).resolve().parents[2]
_CEREBRUM_KEYS = (
    "[cerebrum]",
    "llama_server_bin",
    "gguf_path",
    "ctx_size",
    "startup_probe",
    "restart_max",
)


class TestServeApp:
    def test_serve_app_mounts_endpoints_and_health(self):
        kernel = Kernel.from_config(REPO_ROOT / "configs" / "jev.toml")
        try:
            app = build_serve_app(kernel)
            client = TestClient(app)

            resp = client.get("/health")
            assert resp.status_code == 200
            body = resp.json()
            # /health 五字段契约（design §4.3.1）；大脑按需拉起——未启动 cerebrum=false
            assert set(body) == {
                "status", "cerebellum", "cerebrum", "breaker", "uptime",
            }
            assert body["status"] == "starting"  # 配置了大脑且未拉起
            assert body["cerebellum"] is True
            assert body["cerebrum"] is False
            assert body["breaker"] == "closed"
            assert body["uptime"] >= 0

            # 小脑决策端点真实可用（真 laya 批量前向）
            decision = client.post(
                "/v1/systemone",
                json={
                    "model": "jev",
                    "state": "介绍你自己",
                    "questions": {
                        "intent": {
                            "type": "choice",
                            "instructions": "该请求的意图？",
                            "criteria": {"decision": "决策类", "simple": "简单", "complex": "复杂"},
                        }
                    },
                },
            )
            assert decision.status_code == 200
            body = decision.json()
            answer = body["answers"]["intent"]
            assert answer["choice"] in {"decision", "simple", "complex"}

            # chat 端点已挂载：未拉起大脑时生成路径 503（direct 路由则 200），
            # 但绝非 404（未挂载）
            chat = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "用一句话介绍你自己"}]},
            )
            assert chat.status_code in {200, 503}

            # 生成超时配置透传（EX-04）：[cascade] generation_timeout_s 单源
            assert kernel.generation_timeout_s == 120.0
        finally:
            kernel.close()

    def test_health_ok_without_brain_config(self, tmp_path):
        """无 [cerebrum] 配置：cerebrum=false 但 status=ok（direct/systemone 可用）。"""
        (tmp_path / "decision-points.toml").write_bytes(
            (REPO_ROOT / "configs" / "decision-points.toml").read_bytes()
        )
        (tmp_path / "jev.toml").write_text(
            "\n".join(
                line
                for line in (REPO_ROOT / "configs" / "jev.toml")
                .read_text(encoding="utf-8")
                .splitlines()
                if not line.startswith(_CEREBRUM_KEYS)
            ),
            encoding="utf-8",
        )
        kernel = Kernel.from_config(tmp_path / "jev.toml")
        try:
            client = TestClient(build_serve_app(kernel))
            body = client.get("/health").json()
            assert body["status"] == "ok"
            assert body["cerebrum"] is False
        finally:
            kernel.close()
