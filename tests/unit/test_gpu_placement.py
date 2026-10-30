"""AR016 · GPU 服务放置接线。"""

import pytest

from jev.errors import JevProtocolError
from jev.kernel.kernel import _build_brain_manager


def _config(layers: int) -> dict:
    return {
        "cerebrum": {
            "llama_server_bin": "models/llama-server/bin/llama-server.exe",
            "gguf_path": "models/qwen/test.gguf",
            "gpu_layers": layers,
        }
    }


def test_brain_manager_passes_gpu_layers():
    manager = _build_brain_manager(_config(99))
    assert manager is not None
    cmd = manager._build_cmd()
    index = cmd.index("--n-gpu-layers")
    assert cmd[index + 1] == "99"


def test_brain_manager_defaults_to_cpu():
    manager = _build_brain_manager({"cerebrum": {"gpu_layers": 0}})
    assert manager is not None
    cmd = manager._build_cmd()
    index = cmd.index("--n-gpu-layers")
    assert cmd[index + 1] == "0"


def test_negative_gpu_layers_rejected():
    with pytest.raises(JevProtocolError):
        _build_brain_manager(_config(-1))
