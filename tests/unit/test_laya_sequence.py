"""T011-b：Laya 序列构造器纯逻辑测试（laya_sequence.py）。

移植 receptron/laya sequence.ts（checkpoint rl_common.build_sequence 的模型无关端口）：
序列布局 `[CLS] <type> question: 指令 [SEP] ⟨mask⟩选项0 ⟨mask⟩选项1 … [SEP] state [SEP]`，
marker=各选项 ⟨mask⟩ 位；温度按 (qtype, 选项数分桶) 查表并 clamp 到 [0.5, 5]。
tokenizer 用真 tokenizers 库 + 微型 WordPiece 词表（真库微型资产，非 mock）。
"""

import pytest
from tokenizers import Tokenizer, models, pre_tokenizers

from jev.kernel.cerebellum.base import (
    ChoiceQuestion,
    NoulQuestion,
    ScoreQuestion,
)
from jev.kernel.cerebellum.laya_sequence import (
    MASK_TOKEN,
    SpecialIds,
    build_sequence,
    clamp_temperature,
    render_options,
    scrub,
    temp_bucket,
    temperature_for,
)

VOCAB = {
    "[PAD]": 0,
    "[UNK]": 1,
    "[CLS]": 2,
    "[SEP]": 3,
    MASK_TOKEN: 4,
    "hello": 5,
    "world": 6,
    "jev": 7,
    "deep": 8,
    "fast": 9,
    "off": 10,
    "low": 11,
    "mid": 12,
    "high": 13,
}

IDS = SpecialIds(cls=2, sep=3, mask=4, pad=0, mask_token=MASK_TOKEN)


@pytest.fixture()
def encode():
    tokenizer = Tokenizer(models.WordPiece(vocab=VOCAB, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    return lambda text: tokenizer.encode(text, add_special_tokens=False).ids


# ---------------------------------------------------------------------------
# render_options：三型问题的选项文本
# ---------------------------------------------------------------------------


def test_render_options_choice_with_and_without_descriptions():
    q = ChoiceQuestion(
        choices={"fast": "快速路径", "off": None}, instructions="选择路由"
    )
    assert render_options(q) == ["fast: 快速路径", "off"]


def test_render_options_score_levels_prefixed_with_index():
    q = ScoreQuestion(criteria=["low", "mid", "high"], instructions="评级")
    assert render_options(q) == ["level 0: low", "level 1: mid", "level 2: high"]


def test_render_options_noul_defaults_and_custom_criteria():
    plain = NoulQuestion(instructions="判断")
    assert render_options(plain) == [
        "false: no, the statement does not hold",
        "true: yes, the statement holds",
    ]
    custom = NoulQuestion(
        instructions="判断", criteria={"true": "成立", "false": "不成立"}
    )
    assert render_options(custom) == ["false: 不成立", "true: 成立"]


# ---------------------------------------------------------------------------
# scrub / 温度分桶与 clamp
# ---------------------------------------------------------------------------


def test_scrub_removes_literal_mask_token_from_user_text():
    assert scrub(f"a {MASK_TOKEN} b {MASK_TOKEN} c", MASK_TOKEN) == "a   b   c"
    assert scrub("no marker here", MASK_TOKEN) == "no marker here"


def test_temp_bucket_buckets_by_qtype_and_option_count():
    assert temp_bucket("choice", 1) == "choice:2"
    assert temp_bucket("choice", 2) == "choice:2"
    assert temp_bucket("choice", 5) == "choice:3-5"
    assert temp_bucket("choice", 10) == "choice:6-10"
    assert temp_bucket("choice", 11) == "choice:11+"
    assert temp_bucket("score", 3) == "score:3-5"
    assert temp_bucket("noul", 2) == "noul:2"


def test_clamp_temperature_bounds_and_fallback():
    assert clamp_temperature(0.1) == 0.5
    assert clamp_temperature(10.0) == 5.0
    assert clamp_temperature(1.7) == 1.7
    assert clamp_temperature("bad", fallback=1.0) == 1.0
    assert clamp_temperature(float("nan")) == 1.0


def test_temperature_for_prefers_bucket_then_qtype_base_then_default():
    config = {
        "temperature": [1.1, 1.2, 1.3],
        "temperature_by_options": {"choice:2": 2.0, "bad": 0.1},
    }
    assert temperature_for(config, "choice", 2) == 2.0  # 桶命中
    assert temperature_for(config, "choice", 5) == 1.1  # 回退 qtype 基准
    assert temperature_for(config, "noul", 2) == 1.3
    empty = {"temperature": [], "temperature_by_options": {}}
    assert temperature_for(empty, "choice", 3) == 1.0  # 全缺省 → 1


# ---------------------------------------------------------------------------
# build_sequence：布局与 marker 位置
# ---------------------------------------------------------------------------


def _choice_q() -> ChoiceQuestion:
    return ChoiceQuestion(choices={"fast": None, "deep": None}, instructions="选择路由")


def test_build_sequence_layout_and_marker_positions(encode):
    built = build_sequence(encode, IDS, "hello world", _choice_q(), max_len=64, head_max_len=24)

    seq, markers = built.ids, built.markers
    assert seq[0] == IDS.cls
    assert built.state_truncated is False
    # 布局：[CLS] head [SEP] ⟨mask⟩opt0 ⟨mask⟩opt1 [SEP] state [SEP]
    assert seq.count(IDS.sep) == 3
    for marker in markers:
        assert seq[marker] == IDS.mask  # marker 指向各选项的 ⟨mask⟩ 位
    assert len(markers) == 2
    assert markers[1] - markers[0] > 1  # 两个选项各自有 mask
    # 状态（string）保前部：hello 在 world 前
    assert seq[-3 : -1] == encode("hello world")
    assert IDS is not None and seq[-1] == IDS.sep
    # head 含类型前缀 "choice question: ..."
    assert seq[1] != IDS.mask


def test_build_sequence_long_state_keeps_tail_and_flags_truncation(encode):
    long_state = "hello world jev deep fast off low mid high"
    built = build_sequence(encode, IDS, long_state, _choice_q(), max_len=18, head_max_len=16)

    assert built.state_truncated is True
    assert len(built.ids) <= 18
    assert built.markers and all(m < 18 for m in built.markers)
    # state 截断保尾部：末尾 state token 紧邻收尾 [SEP]
    assert built.ids[-2] == encode("high")[0]
    assert encode("hello")[0] not in built.ids[-6:-1]


def test_build_sequence_options_over_budget_shrink_evenly(encode):
    # 6 个选项在 head_max_len=16 内放不下（每选项至少 ⟨mask⟩+4 token）→ 均匀收缩
    q = ChoiceQuestion(
        choices={f"opt{i}": None for i in range(6)}, instructions="选择"
    )
    built = build_sequence(encode, IDS, "hello", q, max_len=64, head_max_len=16)

    assert len(built.markers) == 6  # 收缩后选项仍全部保留


def test_build_sequence_marks_only_fit_within_max_len(encode):
    built = build_sequence(encode, IDS, "hello world", _choice_q(), max_len=12, head_max_len=16)
    assert all(marker < 12 for marker in built.markers)


def test_build_sequence_scrubs_mask_token_from_state_and_options(encode):
    # 用户文本中的字面 mask token 不可注入 marker 位
    built = build_sequence(
        encode, IDS, "hello erung world", _choice_q(), max_len=64, head_max_len=24
    )
    # ⟨mask⟩ 只能来自选项前缀：出现次数 == 选项数
    assert built.ids.count(IDS.mask) == len(built.markers)
