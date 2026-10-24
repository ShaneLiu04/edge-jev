"""T015：jev doctor 环境体检——全部真实探测（本机资产/GPU/端口）。

约束 C1：禁 mock。体检项全部走真实子进程（nvidia-smi/llama-server --version）、
真实文件系统与 socket 绑定；断言基于本开发机环境（资产就位/GPU 存在）。
"""

import socket
from pathlib import Path

from jev.cli.doctor import render_checks, run_doctor

REPO_ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _write_config(tmp_path: Path, *, port: int, model_path: Path | None) -> Path:
    model = model_path.as_posix() if model_path is not None else "models/laya/laya.onnx"
    ledger = (tmp_path / "ledger").as_posix()
    config = tmp_path / "doctor.toml"
    config.write_text(
        f"""
[server]
host = "127.0.0.1"
port = {port}

[cerebellum]
default_backend = "laya_onnx"

[cerebellum.laya_onnx]
model_path = "{model}"

[ledger]
dir = "{ledger}"
""",
        encoding="utf-8",
    )
    return config


class TestDoctor:
    def test_repo_config_green_on_provisioned_machine(self):
        """本机资产就位 + GPU 存在：全项 PASS/WARN、无 FAIL、exit 0。"""
        checks, exit_code = run_doctor(REPO_ROOT / "configs" / "jev.toml")
        assert exit_code == 0
        by_name = {check.name: check for check in checks}
        assert by_name["Python"].status == "pass"
        assert by_name["onnxruntime"].status == "pass"
        # 开发机 GPU 存在（nvidia-smi 可用）
        assert by_name["GPU"].status == "pass"
        assert by_name["模型资产·小脑"].status == "pass"
        assert by_name["llama-server"].status == "pass"
        assert by_name["cudart"].status == "pass"
        assert by_name["服务端口"].status == "pass"
        assert by_name["台账目录"].status == "pass"

    def test_missing_model_asset_fails_with_fetch_advice(self, tmp_path):
        """资产缺失 → FAIL + `jev fetch` 修复建议 + exit 1（FR9 验收口径）。"""
        config = _write_config(
            tmp_path, port=_free_port(), model_path=tmp_path / "missing.onnx"
        )
        checks, exit_code = run_doctor(config)
        assert exit_code == 1
        cerebellum_check = next(c for c in checks if c.name == "模型资产·小脑")
        assert cerebellum_check.status == "fail"
        assert "jev fetch" in (cerebellum_check.advice or "")

    def test_occupied_server_port_fails(self, tmp_path):
        """服务端口被占 → FAIL + 修改建议 + exit 1。"""
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
            sock.listen(1)
            config = _write_config(tmp_path, port=port, model_path=None)
            checks, exit_code = run_doctor(config)
        assert exit_code == 1
        port_check = next(c for c in checks if c.name == "服务端口")
        assert port_check.status == "fail"
        assert port_check.advice

    def test_render_checks_lists_problems_section(self, tmp_path):
        """问题清单渲染：WARN/FAIL 项集中列出（人类可读输出）。"""
        config = _write_config(
            tmp_path, port=_free_port(), model_path=tmp_path / "missing.onnx"
        )
        checks, _exit_code = run_doctor(config)
        lines = render_checks(checks)
        assert any(line.startswith("[FAIL]") for line in lines)
        assert "问题清单:" in lines
        assert any("建议:" in line for line in lines)
