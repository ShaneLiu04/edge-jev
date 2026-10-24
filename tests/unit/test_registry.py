"""T004：小脑后端注册表测试——真实 TOML 配置文件，禁 mock。

对应实现契约 src/jev/kernel/cerebellum/registry.py：
- create_backend(name, config_path)：读 TOML 配置分发后端
  - "kev" → KevHttpBackend（[cerebellum.kev] serve_url）
  - "laya_onnx" → LayaOnnxBackend（模型缺失时由其自身抛 JevAssetError）
  - 未知名字 → JevProtocolError（消息含合法值列表）
- repo_root()：仓库根（含 configs/jev.toml）
配置一律用真实 TOML 文本（仓库 configs/jev.toml 或 tmp_path 手写文件）。
"""

import pytest

from jev.errors import JevAssetError, JevProtocolError
from jev.kernel.cerebellum.kev_http import KevHttpBackend
from jev.kernel.cerebellum.registry import _load_calibration, create_backend
from jev.kernel.cerebellum.registry import repo_root as find_repo_root


def test_repo_root_resolves_to_repository(repo_root):
    assert (find_repo_root() / "configs" / "jev.toml").is_file()


def test_create_backend_kev_uses_repo_config_serve_url(repo_root):
    backend = create_backend("kev", repo_root / "configs" / "jev.toml")
    assert isinstance(backend, KevHttpBackend)
    assert backend.serve_url == "http://127.0.0.1:8311"


def test_create_backend_kev_uses_custom_serve_url_from_config(tmp_path):
    config = tmp_path / "jev.toml"
    config.write_text(
        '[cerebellum.kev]\nserve_url = "http://10.20.30.40:9999"\n',
        encoding="utf-8",
    )
    backend = create_backend("kev", config)
    assert isinstance(backend, KevHttpBackend)
    assert backend.serve_url == "http://10.20.30.40:9999"


def test_create_backend_laya_onnx_missing_model_raises_asset_error(tmp_path):
    missing_model = (tmp_path / "absent_laya.onnx").as_posix()
    config = tmp_path / "jev.toml"
    config.write_text(
        "[cerebellum.laya_onnx]\n"
        f'model_path = "{missing_model}"\n'
        'calibration_path = "calibration.json"\n',
        encoding="utf-8",
    )
    with pytest.raises(JevAssetError):
        create_backend("laya_onnx", config)


def test_create_backend_unknown_name_raises_protocol_with_valid_names(repo_root):
    with pytest.raises(JevProtocolError) as exc_info:
        create_backend("gpt4o", repo_root / "configs" / "jev.toml")
    message = str(exc_info.value)
    assert "laya_onnx" in message
    assert "kev" in message


# ---------------------------------------------------------------------------
# 校准文件加载（review 修订：[cerebellum.laya_onnx] calibration_path）
# ---------------------------------------------------------------------------


def test_load_calibration_missing_file_warns_and_returns_none(tmp_path, caplog):
    """文件缺失=未发布：警告降级返回 None，不阻断启动。"""
    missing = tmp_path / "not-published.json"
    with caplog.at_level("WARNING", logger="jev.kernel.cerebellum.registry"):
        assert _load_calibration(missing) is None
    assert "未发布" in caplog.text


def test_load_calibration_rejected_report_refuses_to_load(tmp_path):
    """distill 报告 accepted!=true（劣化拒绝发布）→ fail-fast 拒载。"""
    rejected = tmp_path / "calibration.json"
    rejected.write_text(
        '{"kind": "jev-distill-calibration", "accepted": false, "temperature": {"choice": 1.5}}',
        encoding="utf-8",
    )
    with pytest.raises(JevAssetError, match="accepted"):
        _load_calibration(rejected)


def test_load_calibration_accepted_report_takes_temperature_table(tmp_path):
    """accepted=true 的 distill 报告 → 取 temperature 字段为逐 qtype 温度表。"""
    accepted = tmp_path / "calibration.json"
    accepted.write_text(
        '{"kind": "jev-distill-calibration", "accepted": true,'
        ' "temperature": {"choice": 1.25, "score": 2}, "tau": {}}',
        encoding="utf-8",
    )
    assert _load_calibration(accepted) == {"choice": 1.25, "score": 2.0}


def test_load_calibration_bare_mapping_passes_through(tmp_path):
    bare = tmp_path / "calibration.json"
    bare.write_text('{"choice": 1.1, "noul": 0.9}', encoding="utf-8")
    assert _load_calibration(bare) == {"choice": 1.1, "noul": 0.9}


def test_load_calibration_invalid_json_raises_asset_error(tmp_path):
    broken = tmp_path / "calibration.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(JevAssetError, match="合法 JSON"):
        _load_calibration(broken)


def test_load_calibration_non_numeric_temperature_raises_asset_error(tmp_path):
    bad = tmp_path / "calibration.json"
    bad.write_text('{"choice": "hot"}', encoding="utf-8")
    with pytest.raises(JevAssetError, match="温度表"):
        _load_calibration(bad)
