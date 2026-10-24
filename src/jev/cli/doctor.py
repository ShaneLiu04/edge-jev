"""jev doctor 环境体检（FR9）：Python/依赖/GPU/模型资产/llama-server/端口/台账目录。

全部真实探测（子进程 nvidia-smi 与 llama-server --version / 文件系统 /
socket 绑定 / onnxruntime 提供器），无 mock；输出 PASS/WARN/FAIL 清单与
修复建议。exit 0=无 FAIL（WARN 不阻断），1=存在 FAIL。
"""

import os
import shutil
import socket
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

import onnxruntime as ort

from jev.kernel.cerebellum.registry import repo_root

_STATUS_ICON = {"pass": "[PASS]", "warn": "[WARN]", "fail": "[FAIL]"}


@dataclass
class Check:
    """单项体检结果：状态/明细/修复建议。"""

    name: str
    status: str  # pass | warn | fail
    detail: str
    advice: str | None = None


def run_doctor(config_path: Path | str) -> tuple[list[Check], int]:
    """执行全部体检项；返回 (检查清单, 退出码)。"""
    config_path = Path(config_path)
    checks: list[Check] = [
        _check_python(),
        _check_onnxruntime(),
        _check_gpu(),
    ]
    if not config_path.is_file():
        checks.append(
            Check("配置文件", "fail", f"不存在: {config_path}", "检查 --config 路径")
        )
        return checks, 1

    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as exc:
        checks.append(Check("配置文件", "fail", f"不是合法 TOML: {exc}", "修复配置文件"))
        return checks, 1

    checks.extend(_check_assets(config, config_path))
    checks.extend(_check_llama_server(config, config_path))
    checks.extend(_check_ports(config))
    checks.append(_check_ledger_dir(config))

    exit_code = 0 if all(check.status != "fail" for check in checks) else 1
    return checks, exit_code


def render_checks(checks: list[Check]) -> list[str]:
    """渲染为人类可读行（CLI echo 用）。"""
    lines: list[str] = []
    for check in checks:
        lines.append(f"{_STATUS_ICON[check.status]} {check.name}: {check.detail}")
        if check.advice:
            lines.append(f"       建议: {check.advice}")
    problems = [c for c in checks if c.status in ("warn", "fail")]
    if problems:
        lines.append("")
        lines.append("问题清单:")
        for check in problems:
            lines.append(f"  - [{check.status}] {check.name}: {check.advice or check.detail}")
    return lines


# ---------------------------------------------------------------------------
# 各检查项（全部真实探测）
# ---------------------------------------------------------------------------


def _check_python() -> Check:
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    # doctor 可脱离包管理器运行（脚本内嵌环境），故保留显式版本体检
    if sys.version_info >= (3, 11):  # noqa: UP036
        return Check("Python", "pass", f"{version} (>=3.11)")
    return Check("Python", "fail", f"{version} < 3.11", "升级至 Python >= 3.11")


def _check_onnxruntime() -> Check:
    providers = list(ort.get_available_providers())
    if "CPUExecutionProvider" in providers:
        # CPU EP 为 AR001 支持口径（性能调查结论：CUDA EP launch 开销主导，见
        # design.md §4.5），CPU 即 pass
        return Check("onnxruntime", "pass", f"providers: {', '.join(providers)}")
    return Check("onnxruntime", "fail", f"无 CPUExecutionProvider: {providers}", "重装 onnxruntime")


def _check_gpu() -> Check:
    nvcuda = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvcuda.dll"
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi is None:
        if nvcuda.is_file():
            return Check(
                "GPU", "warn", "检测到 CUDA 驱动（nvcuda.dll）但无 nvidia-smi",
                "小脑走 CPU EP（修订预算 P50<=600ms 仍满足）",
            )
        return Check(
            "GPU", "warn", "未检测到 NVIDIA GPU/驱动",
            "小脑走 CPU EP（修订预算 P50<=600ms 仍满足）；大脑 llama-server 需要 GPU",
        )
    try:
        result = subprocess.run(
            [nvidia_smi, "--query-gpu=name,driver_version,memory.total",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return Check("GPU", "warn", "nvidia-smi 执行失败", "检查 GPU 驱动安装")
    if result.returncode != 0 or not result.stdout.strip():
        stderr_tail = result.stderr.strip()[:120]
        return Check("GPU", "warn", f"nvidia-smi 异常: {stderr_tail}", "检查 GPU 驱动")
    return Check("GPU", "pass", result.stdout.strip().splitlines()[0].strip())


def _resolve(config_dir: Path, path_str: str) -> Path:
    path = Path(path_str)
    return path if path.is_absolute() else repo_root() / path


def _check_assets(config: dict, config_path: Path) -> list[Check]:
    checks: list[Check] = []
    cerebellum_cfg = config.get("cerebellum", {})
    laya_cfg = cerebellum_cfg.get("laya_onnx", {})
    model_path = _resolve(config_path.parent, laya_cfg.get("model_path", ""))
    if not model_path:
        checks.append(
            Check("模型资产·小脑", "fail", "未配置 model_path", "在 [cerebellum.laya_onnx] 配置")
        )
        return checks
    if not model_path.is_file():
        checks.append(
            Check(
                "模型资产·小脑", "fail", f"缺失: {model_path}",
                "运行 `jev fetch laya-onnx` 拉取",
            )
        )
        return checks
    # 权重可能为外部数据文件（laya.onnx + laya.onnx.data），体积合并计
    total_bytes = model_path.stat().st_size
    external_data = model_path.with_name(model_path.name + ".data")
    if external_data.is_file():
        total_bytes += external_data.stat().st_size
    size_gb = total_bytes / 1024**3
    checks.append(
        Check("模型资产·小脑", "pass", f"{model_path.name} ({size_gb:.2f}GB) 就位")
    )
    # tokenizer：文件或目录（tokenizer/tokenizer.json）两种落位
    tokenizer_path = model_path.parent / "tokenizer.json"
    tokenizer_dir = model_path.parent / "tokenizer"
    if not (tokenizer_path.is_file() or (tokenizer_dir / "tokenizer.json").is_file()):
        checks.append(
            Check(
                "模型资产·小脑", "fail", f"缺失 tokenizer: {tokenizer_dir}",
                "运行 `jev fetch laya-onnx` 完整拉取",
            )
        )
    if not (model_path.parent / "laya_config.json").is_file():
        checks.append(
            Check(
                "模型资产·小脑", "fail",
                f"缺失: {model_path.parent / 'laya_config.json'}",
                "运行 `jev fetch laya-onnx` 完整拉取",
            )
        )
    return checks


def _check_llama_server(config: dict, config_path: Path) -> list[Check]:
    checks: list[Check] = []
    cerebrum_cfg = config.get("cerebrum")
    if not cerebrum_cfg:
        checks.append(
            Check("llama-server", "warn", "未配置 [cerebrum]（仅小脑决策服务）")
        )
        return checks
    bin_path = _resolve(config_path.parent, cerebrum_cfg.get(
        "llama_server_bin", "models/llama-server/bin/llama-server.exe"
    ))
    gguf_path = _resolve(config_path.parent, cerebrum_cfg.get(
        "gguf_path", "models/qwen/qwen2.5-1.5b-instruct-q4_k_m.gguf"
    ))
    if not bin_path.is_file():
        checks.append(
            Check(
                "llama-server", "fail", f"二进制缺失: {bin_path}",
                "运行 `jev fetch llama-server`",
            )
        )
    if not gguf_path.is_file():
        checks.append(
            Check("大脑 GGUF", "fail", f"缺失: {gguf_path}", "运行 `jev fetch qwen-gguf`")
        )
    if bin_path.is_file():
        try:
            result = subprocess.run(
                [str(bin_path), "--version"], capture_output=True, text=True,
                timeout=15, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            checks.append(
                Check(
                    "llama-server", "fail", f"--version 执行失败: {exc}",
                    "运行 `jev doctor` 复检",
                )
            )
        else:
            output = (result.stdout or result.stderr).strip().splitlines()
            version_line = output[0] if output else f"exit={result.returncode}"
            checks.append(Check("llama-server", "pass", f"{bin_path.name}: {version_line[:80]}"))
        cudart_dir = bin_path.parent.parent / "cudart"
        if cudart_dir.is_dir() and any(cudart_dir.glob("cudart*.dll")):
            checks.append(Check("cudart", "pass", f"{cudart_dir} 就位"))
        else:
            checks.append(
                Check(
                    "cudart", "fail", f"缺失 CUDA 运行时 DLL 目录: {cudart_dir}",
                    "运行 `jev fetch llama-server-cudart`（llama-server 启动依赖）",
                )
            )
    return checks


def _check_ports(config: dict) -> list[Check]:
    checks: list[Check] = []
    server_cfg = config.get("server", {})
    host = str(server_cfg.get("host", "127.0.0.1"))
    port = int(server_cfg.get("port", 8300))
    checks.append(_port_check("服务端口", host, port, fail_on_occupied=True))
    cerebrum_cfg = config.get("cerebrum")
    if cerebrum_cfg:
        brain_port = int(cerebrum_cfg.get("port", 8388))
        checks.append(_port_check("大脑端口", host, brain_port, fail_on_occupied=False))
    return checks


def _port_check(name: str, host: str, port: int, *, fail_on_occupied: bool) -> Check:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
    except OSError:
        status = "fail" if fail_on_occupied else "warn"
        advice = (
            f"释放 {host}:{port} 或在 [server] 修改 port"
            if fail_on_occupied
            else "可能为运行中的 llama-server（复用）或残留进程（请结束）"
        )
        return Check(name, status, f"{host}:{port} 已被占用", advice)
    return Check(name, "pass", f"{host}:{port} 可绑定")


def _check_ledger_dir(config: dict) -> Check:
    ledger_cfg = config.get("ledger")
    if not ledger_cfg:
        return Check("台账目录", "warn", "未配置 [ledger]（不落盘）")
    ledger_dir = Path(ledger_cfg.get("dir", "data/ledger"))
    if not ledger_dir.is_absolute():
        ledger_dir = repo_root() / ledger_dir
    try:
        ledger_dir.mkdir(parents=True, exist_ok=True)
        probe = ledger_dir / ".doctor-write-probe"
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return Check(
            "台账目录", "fail", f"不可写: {ledger_dir} ({exc})", "检查目录权限/磁盘"
        )
    return Check("台账目录", "pass", f"{ledger_dir} 可写")
