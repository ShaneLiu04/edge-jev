"""AR008：投机解码参数、长生成数据集、矩阵配置（纯逻辑）。"""

import pytest

from jev.bench.generation import (
    build_long_generation,
    build_long_generation_en,
    build_shared_generation,
)
from jev.bench.matrix import _spec_n, load_matrix_config
from jev.errors import JevAssetError
from jev.kernel.cerebrum.llama_server import spec_server_args


def test_spec_args_disabled_is_empty():
    assert spec_server_args(enabled=False, draft_model="x") == []


def test_spec_args_enabled_contains_cpu_draft(tmp_path):
    draft = tmp_path / "draft.gguf"
    draft.write_bytes(b"gguf")
    args = spec_server_args(
        enabled=True,
        draft_model=draft,
        draft_n_max=6,
        draft_gpu_layers=0,
        draft_threads=4,
    )
    assert "--spec-draft-model" in args
    assert str(draft) in args
    assert "--spec-type" in args and "draft-simple" in args
    assert args[args.index("--spec-draft-n-max") + 1] == "6"
    assert args[args.index("--spec-draft-ngl") + 1] == "0"
    assert args[args.index("--spec-draft-threads") + 1] == "4"


def test_spec_args_missing_asset_fails_loudly(tmp_path):
    with pytest.raises(JevAssetError):
        spec_server_args(enabled=True, draft_model=tmp_path / "missing.gguf")
    with pytest.raises(ValueError):
        spec_server_args(enabled=True, draft_model=tmp_path / "x", draft_n_max=0)


def test_shared_generation_dataset_is_prefix_stable():
    first = build_shared_generation(8)
    second = build_shared_generation(8)
    assert [q.prompt for q in first] == [q.prompt for q in second]
    prefixes = {q.prompt.split("\n\n问题：", 1)[0] for q in first}
    assert len(prefixes) == 1
    for index, question in enumerate(first, start=1):
        assert question.id == f"gen-{index:02d}"
        assert question.gold.kind == "contains"
        assert question.gold.value in question.prompt
        assert "至少 80 个字" in question.prompt
        assert len(question.prompt) > 3000


def test_spec_experiment_config():
    config = load_matrix_config("configs/experiments/spec-decode.toml")
    assert config.dataset == "shared_generation"
    assert config.force_full is True
    assert config.prompt_cache is True
    assert config.arms == ("B1", "SPEC", "CACHE", "CACHE_SPEC")
    spec = config.overlays["SPEC"]["cerebrum"]["spec"]
    assert spec["enabled"] is True
    assert spec["draft_gpu_layers"] == 0
    assert config.overlays["CACHE_SPEC"]["cerebrum"]["spec"]["enabled"] is True


def test_spec_header_parsing():
    assert _spec_n("100/202", 0) == 100
    assert _spec_n("100/202", 1) == 202
    assert _spec_n("bad", 0) == 0
    assert _spec_n(None, 1) == 0


def test_long_generation_dataset_is_short_prompt_long_output():
    questions = build_long_generation(12)
    assert len(questions) == 12
    for index, question in enumerate(questions, start=1):
        assert question.id == f"long-{index:02d}"
        assert question.gold.kind == "contains"
        assert question.gold.value in question.prompt
        assert len(question.prompt) < 200
        assert "至少 100 字" in question.prompt
    assert questions[0].gold.value != questions[1].gold.value


def test_long_generation_en_dataset_is_content_guarded():
    questions = build_long_generation_en(4)
    assert len(questions) == 4
    for index, question in enumerate(questions, start=1):
        assert question.id == f"longen-{index:02d}"
        assert question.lang == "en"
        assert question.gold.kind == "regex"
        assert question.gold.value == "(?i)(hit|reuse)"
        assert "hit rate" in question.prompt.lower()


def test_greedy_sampling_arg(tmp_path):
    draft = tmp_path / "draft.gguf"
    draft.write_bytes(b"gguf")
    args = spec_server_args(
        enabled=True, draft_model=draft, sampling="greedy"
    )
    assert args[args.index("--spec-draft-sampling") + 1] == "greedy"
