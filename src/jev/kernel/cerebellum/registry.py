"""小脑后端注册表：按配置名分发后端实例（configs/jev.toml [cerebellum]）。"""

import json
import logging
import tomllib
from pathlib import Path

from jev.errors import JevAssetError, JevProtocolError
from jev.kernel.cerebellum.base import CerebellumBackend
from jev.kernel.cerebellum.kev_http import KevHttpBackend
from jev.kernel.cerebellum.laya_onnx import LayaOnnxBackend

logger = logging.getLogger(__name__)

_VALID_BACKENDS = ("laya_onnx", "kev")


def repo_root() -> Path:
    """仓库根（src/jev/kernel/cerebellum/registry.py 向上 5 级）。"""
    return Path(__file__).resolve().parents[4]


def create_backend(name: str, config_path: Path | str) -> CerebellumBackend:
    """按配置构建小脑后端；未知名 → JevProtocolError（含合法值列表）。"""
    if name not in _VALID_BACKENDS:
        raise JevProtocolError(
            f"未知小脑后端: {name}（合法值: {', '.join(_VALID_BACKENDS)}）"
        )

    config_file = Path(config_path)
    if not config_file.is_file():
        raise JevAssetError(f"配置文件不存在: {config_file}")
    with config_file.open("rb") as f:
        config = tomllib.load(f)

    cerebellum_cfg = config.get("cerebellum", {})
    if name == "kev":
        kev_cfg = cerebellum_cfg.get("kev", {})
        serve_url = kev_cfg.get("serve_url", "http://127.0.0.1:8311")
        return KevHttpBackend(serve_url=serve_url)

    laya_cfg = cerebellum_cfg.get("laya_onnx", {})
    model_path = _resolve(laya_cfg.get("model_path", "models/laya/laya.onnx"))
    model_dir = model_path.parent
    default_tokenizer = model_dir / "tokenizer" / "tokenizer.json"
    tokenizer_path = _resolve(laya_cfg.get("tokenizer_path", default_tokenizer))
    bundle_config_path = _resolve(laya_cfg.get("config_path", model_dir / "laya_config.json"))
    return LayaOnnxBackend(
        model_path=model_path,
        tokenizer_path=tokenizer_path,
        config_path=bundle_config_path,
        calibration=_load_calibration(laya_cfg.get("calibration_path")),
    )


def create_laya_int8(
    config_path: Path | str, *, max_state_tokens: int | None = None
) -> CerebellumBackend | None:
    """构建可选 int8 Laya 决策后端；路径为空/文件不存在返回 None。"""
    config_file = Path(config_path)
    if not config_file.is_file():
        raise JevAssetError(f"配置文件不存在: {config_file}")
    with config_file.open("rb") as f:
        config = tomllib.load(f)
    laya_cfg = (config.get("cerebellum", {}) or {}).get("laya_onnx", {}) or {}
    raw = str(laya_cfg.get("int8_model_path", "") or "").strip()
    if not raw:
        return None
    int8_path = _resolve(raw)
    if not int8_path.is_file():
        logger.info("int8 Laya 未就位，跳过精度分层: %s", int8_path)
        return None
    fp32_path = _resolve(laya_cfg.get("model_path", "models/laya/laya.onnx"))
    model_dir = fp32_path.parent
    tokenizer_path = _resolve(
        laya_cfg.get("tokenizer_path", model_dir / "tokenizer" / "tokenizer.json")
    )
    bundle_config_path = _resolve(
        laya_cfg.get("config_path", model_dir / "laya_config.json")
    )
    return LayaOnnxBackend(
        model_path=int8_path,
        tokenizer_path=tokenizer_path,
        config_path=bundle_config_path,
        calibration=_load_calibration(laya_cfg.get("calibration_path")),
        max_state_tokens=max_state_tokens,
    )


def _load_calibration(calibration_path: str | Path | None) -> dict[str, float] | None:
    """蒸馏校准文件（T019 产物）→ 逐 qtype 温度表。

    支持两种形状：distill 校准报告（kind=jev-distill-calibration，取
    temperature 字段，rejected 文件拒绝加载）与裸 {qtype: 温度} 映射。
    文件缺失视为未发布（警告 + 不校准——蒸馏闭环的可选增强）；显式配置
    但内容非法/被拒绝 → fail-fast。
    """
    if not calibration_path:
        return None
    path = _resolve(calibration_path)
    if not path.is_file():
        logger.warning("校准文件未发布（%s），小脑以基准温度运行", path)
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise JevAssetError(f"校准文件不是合法 JSON（{path}）: {exc}") from exc
    if not isinstance(payload, dict):
        raise JevAssetError(f"校准文件须为 JSON 对象（{path}）")
    if payload.get("kind") == "jev-distill-calibration":
        if payload.get("accepted") is not True:
            raise JevAssetError(
                f"校准文件未通过发布验收（accepted!=true），拒绝加载: {path}"
            )
        temperature = payload.get("temperature")
    else:
        temperature = payload
    if not isinstance(temperature, dict) or not all(
        isinstance(v, (int, float)) for v in temperature.values()
    ):
        raise JevAssetError(f"校准温度表须为 {{qtype: 数值}}（{path}）")
    return {str(k): float(v) for k, v in temperature.items()}


def _resolve(path: Path | str) -> Path:
    """相对路径以仓库根解析。"""
    resolved = Path(path)
    return resolved if resolved.is_absolute() else repo_root() / resolved
