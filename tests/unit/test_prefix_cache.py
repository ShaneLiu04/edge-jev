"""AR007：共享前缀数据集、稳定前缀策略、缓存命中解析（纯逻辑）。"""

from jev.bench.matrix import _cache_n, load_matrix_config
from jev.bench.prefixset import build_shared_prefix
from jev.kernel.runtime.prefixcache import use_stable_prefix


def test_shared_prefix_dataset_is_deterministic_and_shared():
    first = build_shared_prefix(12)
    second = build_shared_prefix(12)
    assert [q.prompt for q in first] == [q.prompt for q in second]
    prefixes = {q.prompt.split("\n\n问题：", 1)[0] for q in first}
    assert len(prefixes) == 1
    assert len(first) == 12
    for index, question in enumerate(first, start=1):
        assert question.id == f"shared-{index:02d}"
        assert question.gold.kind == "numeric"
        assert question.gold.value in question.prompt
        assert question.prompt.endswith("只输出数字。")


def test_shared_prefix_targets_are_not_first_or_last():
    questions = build_shared_prefix(6, paragraphs=12)
    document = questions[0].prompt.split("\n\n问题：", 1)[0]
    paragraphs = [part for part in document.split("\n\n") if part]
    for question in questions:
        target = question.gold.value
        position = next(
            index for index, part in enumerate(paragraphs) if target in part
        )
        assert position not in (0, len(paragraphs) - 1)


def test_use_stable_prefix_truth_table():
    assert use_stable_prefix(False, True, "s") is False
    assert use_stable_prefix(True, False, "s") is False
    assert use_stable_prefix(True, True, "") is False
    assert use_stable_prefix(True, True, "session-1") is True


def test_prefix_cache_experiment_config():
    config = load_matrix_config("configs/experiments/prefix-cache.toml")
    assert config.dataset == "shared_prefix"
    assert config.prompt_cache is True
    assert config.arms == ("B1", "CACHE", "AEB", "AEB_CACHE")
    assert config.overlays["CACHE"]["runtime"]["prefix_cache"] is True


def test_cache_n_header_parsing():
    assert _cache_n("hit:1365") == 1365
    assert _cache_n("miss") == 0
    assert _cache_n("off") == 0
    assert _cache_n(None) == 0
    assert _cache_n("hit:bad") == 0


def test_portfolio_profiles_parse():
    import tomllib

    from jev.kernel.runtime.portfolio import load_profile, merge_profile

    base = tomllib.loads(
        open("configs/jev.toml", encoding="utf-8").read()
    )
    aeb = merge_profile(base, load_profile("configs/profiles/aeb.toml"))
    assert aeb["runtime"]["enabled"] is True
    assert aeb["prefill"]["evidence"] is True
    assert aeb["runtime"]["prefix_cache"] is False

    cache = merge_profile(base, load_profile("configs/profiles/prefix-cache.toml"))
    assert cache["runtime"]["prefix_cache"] is True
    assert cache["runtime"]["session_affinity"] is True

    short = merge_profile(base, load_profile("configs/profiles/short-qa.toml"))
    assert short["runtime"]["enabled"] is False
