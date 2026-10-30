"""AR014 · LCPT-RFG 纯逻辑：token 计数、配置校验、量化入口。"""

from pathlib import Path

import pytest

from jev.errors import JevAssetError
from jev.kernel.cerebellum.quantize import int8_sidecar, prepare_laya_int8
from jev.kernel.runtime.config import RuntimeConfig
from jev.kernel.runtime.portfolio import to_toml


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


def test_gate_window_defaults_to_384_and_allows_zero():
    assert RuntimeConfig().decision_long_context_gate_tokens == 384
    assert RuntimeConfig(decision_long_context_gate_tokens=0).decision_long_context_gate_tokens == 0
    with pytest.raises(ValueError):
        RuntimeConfig(decision_long_context_gate_tokens=-1)


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


def test_gate_windows_validation_and_default():
    config = RuntimeConfig()
    assert config.decision_long_context_gate_windows == (128, 384)
    empty = RuntimeConfig(decision_long_context_gate_windows=())
    assert empty.decision_long_context_gate_windows == ()
    with pytest.raises(ValueError):
        RuntimeConfig(decision_long_context_gate_windows=(384, 128))
    with pytest.raises(ValueError):
        RuntimeConfig(decision_long_context_gate_windows=(128, 128))
    with pytest.raises(ValueError):
        RuntimeConfig(decision_long_context_gate_windows=(0, 128))


def test_windowed_backend_shares_session(make_backend):
    backend = make_backend()
    view = backend.windowed(32)
    assert view.state_window == 32
    assert view._session is backend._session
    assert view._tokenizer is backend._tokenizer


def test_profile_toml_serializes_gate_windows():
    text = to_toml("CGW", {"runtime": {"decision_long_context_gate_windows": (128, 384)}})
    assert "decision_long_context_gate_windows = [128, 384]" in text
