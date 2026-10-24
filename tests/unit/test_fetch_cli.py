"""T002 `jev fetch` CLI 单元测试（typer.testing.CliRunner，进程内真实调用，无 mock）。

真实下载行为由 test_downloader.py 用本地真实 HTTP 服务器覆盖；本文件覆盖 CLI 装配：
帮助文本 / 未知资产错误 / 已就位资产零网络快速跳过（退出码 0）。
"""

import pathlib

import pytest
from jev.fetch.manifest import load_manifest
from typer.testing import CliRunner

from jev.cli import app

runner = CliRunner()

_PLACEHOLDER = b"unit-test-placeholder"


def _output_with_stderr(result) -> str:
    """兼容 click 8.1（stderr 混入 output）与 8.2+（stderr 分离）两种形态。"""
    try:
        return result.output + result.stderr
    except ValueError:
        return result.output


def _prune_empty_dirs(directory: pathlib.Path, stop: pathlib.Path) -> None:
    current = directory
    while current != stop and stop in current.parents:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def test_fetch_help_exits_zero_and_shows_fetch_usage():
    result = runner.invoke(app, ["fetch", "--help"])

    assert result.exit_code == 0
    assert "fetch" in result.output


def test_fetch_help_documents_all_and_force_options():
    result = runner.invoke(app, ["fetch", "--help"])

    assert result.exit_code == 0
    assert "--all" in result.output
    assert "--force" in result.output


def test_fetch_unknown_asset_exits_nonzero_and_names_the_asset():
    result = runner.invoke(app, ["fetch", "definitely-not-an-asset"])

    assert result.exit_code != 0
    assert "definitely-not-an-asset" in _output_with_stderr(result)


@pytest.fixture
def laya_onnx_in_place(repo_root: pathlib.Path):
    """预置 laya-onnx 目标文件 → fetch 走"已就位跳过"路径，零网络请求退出 0。"""
    dest = repo_root / "models" / "laya" / "laya.onnx"
    created = not dest.exists()
    dest.parent.mkdir(parents=True, exist_ok=True)
    if created:
        dest.write_bytes(_PLACEHOLDER)
    try:
        yield dest, created
    finally:
        if created:
            dest.unlink(missing_ok=True)
            _prune_empty_dirs(dest.parent, repo_root)


def test_fetch_in_place_asset_exits_zero_without_redownload(laya_onnx_in_place):
    dest, created = laya_onnx_in_place

    result = runner.invoke(app, ["fetch", "laya-onnx"])

    assert result.exit_code == 0
    if created:
        assert dest.read_bytes() == _PLACEHOLDER


@pytest.fixture
def all_asset_dests_in_place(repo_root: pathlib.Path):
    """预置 manifest 全部资产目标文件 → `fetch --all` 全部跳过，退出 0 不触网。"""
    specs = load_manifest(repo_root / "configs" / "assets.toml")
    created = []
    for spec in specs:
        dest = repo_root / spec.dest
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(_PLACEHOLDER)
            created.append(dest)
    try:
        yield specs
    finally:
        for dest in created:
            dest.unlink(missing_ok=True)
        for dest in created:
            _prune_empty_dirs(dest.parent, repo_root)


def test_fetch_all_exits_zero_when_all_assets_in_place(all_asset_dests_in_place):
    result = runner.invoke(app, ["fetch", "--all"])

    assert result.exit_code == 0
