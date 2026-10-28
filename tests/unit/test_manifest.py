"""T002 manifest 解析单元测试（UT-05：解析 / 字段校验 / 可定位错误）。"""

import pytest

from jev.errors import JevAssetError
from jev.fetch.manifest import AssetSpec, load_manifest

FULL_MANIFEST = """\
[[asset]]
name = "model-a"
kind = "model"
dest = "models/a.onnx"
url = "https://example.com/a.onnx"
mirrors = ["https://m1.example.com/a.onnx", "https://m2.example.com/a.onnx"]
sha256 = "0123abcd"

[[asset]]
name = "pack-b"
kind = "archive"
dest = "vendor/pack-b"
url = "https://example.com/pack-b.zip"
mirrors = []
sha256 = ""
"""


def _write(tmp_path, text: str, name: str = "assets.toml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_load_manifest_parses_all_asset_fields(tmp_path):
    path = _write(tmp_path, FULL_MANIFEST)

    specs = load_manifest(path)

    first = specs[0]
    assert isinstance(first, AssetSpec)
    assert first.name == "model-a"
    assert first.kind == "model"
    assert first.dest == "models/a.onnx"
    assert first.url == "https://example.com/a.onnx"
    assert first.mirrors == ["https://m1.example.com/a.onnx", "https://m2.example.com/a.onnx"]
    assert first.sha256 == "0123abcd"


def test_load_manifest_keeps_declaration_order_and_allows_empty_fields(tmp_path):
    path = _write(tmp_path, FULL_MANIFEST)

    specs = load_manifest(path)

    assert [s.name for s in specs] == ["model-a", "pack-b"]
    assert specs[1].kind == "archive"
    assert specs[1].mirrors == []
    assert specs[1].sha256 == ""


def test_load_manifest_missing_file_raises_asset_error_naming_path(tmp_path):
    missing = tmp_path / "no-such-manifest.toml"

    with pytest.raises(JevAssetError) as excinfo:
        load_manifest(missing)

    assert "no-such-manifest.toml" in str(excinfo.value)


def test_load_manifest_malformed_toml_raises_asset_error(tmp_path):
    path = _write(tmp_path, "[[asset\nname = broken")

    with pytest.raises(JevAssetError):
        load_manifest(path)


def test_load_manifest_missing_required_field_raises_error_naming_asset(tmp_path):
    # 缺 dest 字段：错误必须可定位到具体资产条目
    path = _write(
        tmp_path,
        "[[asset]]\n"
        'name = "broken"\n'
        'kind = "model"\n'
        'url = "https://example.com/x"\n'
        "mirrors = []\n"
        'sha256 = ""\n',
    )

    with pytest.raises(JevAssetError) as excinfo:
        load_manifest(path)

    assert "broken" in str(excinfo.value)


def test_load_manifest_invalid_field_type_raises_error_naming_asset(tmp_path):
    path = _write(
        tmp_path,
        "[[asset]]\n"
        'name = "bad-kind"\n'
        "kind = 123\n"
        'dest = "models/x"\n'
        'url = "https://example.com/x"\n'
        "mirrors = []\n"
        'sha256 = ""\n',
    )

    with pytest.raises(JevAssetError) as excinfo:
        load_manifest(path)

    assert "bad-kind" in str(excinfo.value)


def test_load_real_assets_manifest_lists_expected_assets(configs_dir):
    specs = load_manifest(configs_dir / "assets.toml")

    assert [s.name for s in specs] == [
        "laya-onnx",
        "laya-onnx-data",
        "laya-config",
        "laya-tokenizer",
        "laya-tokenizer-config",
        "kev-repo",
        "qwen2.5-1.5b-gguf",
        "qwen2.5-0.5b-gguf",
        "llama-server",
        "llama-cudart",
    ]
    for spec in specs:
        assert isinstance(spec.dest, str)
        assert not spec.dest.startswith(("/", "\\"))
