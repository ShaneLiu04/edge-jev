"""AR014 · LCPT-RFG 纯逻辑：token 计数、配置校验、量化入口。"""

from pathlib import Path

import pytest

from jev.errors import JevAssetError
from jev.kernel.cerebellum.quantize import int8_sidecar, prepare_laya_int8
from jev.kernel.runtime.config import RuntimeConfig


def test_count_tokens_matches_tokenizer(make_backend):
    backend = make_backend()
    assert backend.count_tokens("hello world jev") > 0
    assert backend.count_tokens("hello world jev") == backend.count_tokens(
        "hello world jev"
    )


def test_long_context_threshold_defaults_off_and_rejects_negative():
    assert RuntimeConfig().decision_long_context_tokens == 0
    assert RuntimeConfig(decision_long_context_tokens=4096).decision_long_context_tokens == 4096
    with pytest.raises(ValueError):
        RuntimeConfig(decision_long_context_tokens=-1)


def test_prepare_laya_int8_rejects_missing_source(tmp_path):
    with pytest.raises(JevAssetError):
        prepare_laya_int8(tmp_path / "missing.onnx", tmp_path / "out.onnx")


def test_prepare_laya_int8_requires_onnx_output(tmp_path):
    source = tmp_path / "laya.onnx"
    source.write_bytes(b"not a model")
    with pytest.raises(JevAssetError):
        prepare_laya_int8(source, tmp_path / "out.bin")


def test_int8_sidecar_name():
    assert int8_sidecar(Path("models/laya/laya_int8.onnx")).name == "laya_int8.onnx.data"
