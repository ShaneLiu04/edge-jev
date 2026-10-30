"""Laya 决策模型 int8 动态量化（AR014）。

Laya ONNX 带外部权重文件；上游图存在 shape-inference 形状冲突，直接调用
`quantize_dynamic` 会失败。这里先合并外部数据，再在量化期间跳过形状推导并
显式声明默认权重类型，产出可被 onnxruntime 直接加载的 int8 模型。

产物不进入仓库（models/ gitignored），由 `jev prepare-laya-int8` 生成。
"""

from __future__ import annotations

from pathlib import Path

from jev.errors import JevAssetError


def prepare_laya_int8(
    source: Path | str,
    output: Path | str,
    *,
    per_channel: bool = True,
) -> Path:
    """从 fp32 Laya ONNX 生成动态 int8 模型，返回 .onnx 路径。"""
    src = Path(source)
    dst = Path(output)
    if not src.is_file():
        raise JevAssetError(f"Laya fp32 模型不存在: {src}")
    if dst.suffix != ".onnx":
        raise JevAssetError(f"输出必须是 .onnx 文件: {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)

    try:
        import onnx
        import onnxruntime.quantization.onnx_quantizer as onnx_quantizer
        from onnxruntime.quantization import QuantType, quantize_dynamic
    except ImportError as exc:  # pragma: no cover - 依赖缺失属于环境错误
        raise JevAssetError(f"量化依赖不可用（onnx/onnxruntime）: {exc}") from exc

    merged = dst.parent / f"{dst.stem}_merged.onnx"
    try:
        model = onnx.load(str(src), load_external_data=True)
        onnx.save_model(model, str(merged), save_as_external_data=False)
        del model
    except Exception as exc:
        merged.unlink(missing_ok=True)
        raise JevAssetError(f"合并 Laya 外部数据失败: {exc}") from exc

    original = onnx_quantizer.save_and_reload_model_with_shape_infer

    def _skip_shape_inference(current):
        return current

    try:
        onnx_quantizer.save_and_reload_model_with_shape_infer = _skip_shape_inference
        quantize_dynamic(
            str(merged),
            str(dst),
            weight_type=QuantType.QInt8,
            per_channel=per_channel,
            reduce_range=False,
            use_external_data_format=True,
            extra_options={"DefaultTensorType": onnx.TensorProto.FLOAT},
        )
    except Exception as exc:
        raise JevAssetError(f"Laya int8 量化失败: {exc}") from exc
    finally:
        onnx_quantizer.save_and_reload_model_with_shape_infer = original
        merged.unlink(missing_ok=True)

    if not dst.is_file() or dst.stat().st_size == 0:
        raise JevAssetError(f"量化输出为空: {dst}")
    return dst


def int8_sidecar(output: Path | str) -> Path:
    """int8 模型的外部权重文件路径（ORT 约定）。"""
    dst = Path(output)
    return dst.with_name(f"{dst.name}.data")
