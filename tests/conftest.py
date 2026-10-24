"""conftest：全局 fixture 与 marker 注册说明。

markers:
- assets: 需要真实模型资产（Laya ONNX / Qwen GGUF / llama-server），
  仅在 `jev fetch --all` 完成后可运行。
- slow: 长耗时端到端用例（三路径/蒸馏/基准）。
"""

import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def repo_root() -> pathlib.Path:
    return REPO_ROOT


@pytest.fixture
def configs_dir(repo_root: pathlib.Path) -> pathlib.Path:
    return repo_root / "configs"
