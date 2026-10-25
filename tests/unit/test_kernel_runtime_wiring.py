"""AR003 T009：配置装配（runtime/cache/prefill/embeddings）与多槽参数（FR2-2）。"""

import tomllib
from pathlib import Path

import pytest

from jev.kernel.cerebrum.embeddings import EmbeddingService
from jev.kernel.cerebrum.llama_server import slot_server_args
from jev.kernel.kernel import _build_embeddings, _build_runtime

REPO = Path(__file__).resolve().parents[2]


def test_default_config_keeps_everything_off():
    config = tomllib.loads((REPO / "configs" / "jev.toml").read_text(encoding="utf-8"))
    runtime, cache = _build_runtime(config)
    assert runtime.enabled is False
    assert runtime.bands.verifier_enabled is False
    assert runtime.prefill.enabled is False
    assert runtime.prompt.memory_enabled is False
    assert cache is None
    assert _build_embeddings(config) is None


def test_enabled_sections_are_parsed():
    config = {
        "runtime": {
            "enabled": True,
            "tau_low": 0.2,
            "tau_high": 0.8,
            "verifier_enabled": True,
            "self_verify_mode": "gate",
            "gate_threshold": 0.5,
            "memory_enabled": True,
            "keep_turns": 3,
            "draft_injection": True,
            "verifier_max_tokens": 8,
        },
        "cache": {"enabled": True, "similarity": 0.9, "margin": 0.01, "capacity": 8},
        "prefill": {"enabled": True, "budget_ratio": 0.5, "min_prompt_tokens": 100},
        "cerebrum": {"parallel": 2, "slot_prompt_similarity": 0.4},
    }
    runtime, cache = _build_runtime(config)
    assert runtime.enabled is True
    assert runtime.bands.tau_low == 0.2
    assert runtime.bands.self_verify_mode == "gate"
    assert runtime.bands.verifier_enabled is True
    assert runtime.bands.gate_threshold == 0.5
    assert runtime.verifier_max_tokens == 8
    assert runtime.cache.enabled is True
    assert cache is not None
    assert runtime.prefill.enabled is True
    assert runtime.prompt.memory_enabled is True
    assert runtime.prompt.draft_enabled is True
    assert runtime.prompt.keep_turns == 3
    assert runtime.prompt.prefill_enabled is True


def test_invalid_band_order_rejected():
    with pytest.raises(ValueError):
        _build_runtime({"runtime": {"tau_low": 0.9, "tau_high": 0.5}})


def test_embeddings_direct_base_url_never_spawns():
    config = {"embeddings": {"base_url": "http://127.0.0.1:9999", "model": "m"}}
    service = _build_embeddings(config)
    assert isinstance(service, EmbeddingService)
    assert service.base_url == "http://127.0.0.1:9999"
    service.close()


def test_embeddings_model_path_builds_service():
    config = {
        "cerebrum": {"llama_server_bin": "bin/llama-server.exe"},
        "embeddings": {"model_path": "models/embed.gguf", "port": 8399},
    }
    service = _build_embeddings(config)
    assert isinstance(service, EmbeddingService)
    assert "8399" in service.base_url
    service.close()


def test_slot_server_args_only_emit_non_defaults():
    assert slot_server_args(parallel=1) == ["--parallel", "1"]
    assert slot_server_args(parallel=2, slot_prompt_similarity=0.5) == [
        "--parallel",
        "2",
        "--slot-prompt-similarity",
        "0.5",
    ]
    assert slot_server_args(parallel=2, slot_prompt_similarity=0.0) == ["--parallel", "2"]
    with pytest.raises(ValueError):
        slot_server_args(parallel=0)
    with pytest.raises(ValueError):
        slot_server_args(parallel=1, slot_prompt_similarity=2.0)
