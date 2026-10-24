"""T008：决策点框架测试——注册表/三态/声明式 TOML/小脑评估与 tau 门控。

对应实现契约 src/jev/kernel/decisions/framework.py（Red 阶段：模块未实现，
ModuleNotFoundError 属预期）：
- Stage(PRE/MID/POST) / Mode(ACTIVE/SHADOW/OFF) 枚举
- DecisionPoint（frozen；mode 默认 ACTIVE）/ Decision（from_fallback 默认 False）
- DecisionRegistry：register 同 id 覆盖 + 非法字段 JevProtocolError；get 缺失报错
  （消息含 id 与已知清单）；ids/by_stage/active/shadow 声明序；set_mode 运行态切换；
  from_toml 解析 [[point]]（含 question_type/options 扩展字段）并复用同一校验
- to_question 三型映射；evaluate_point 走真实小脑推理；applied_value tau 门控安全侧

约束 C1：evaluate_point 用真实 LayaOnnxBackend（微型 onnx 模型真实推理），
helper 与 test_laya_onnx.py 同款；禁止 unittest.mock。
"""

import dataclasses

import pytest

from jev.errors import JevProtocolError
from jev.kernel.cerebellum.base import ChoiceQuestion, NoulQuestion, ScoreQuestion
from jev.kernel.decisions.framework import (
    Decision,
    DecisionPoint,
    DecisionRegistry,
    Mode,
    Stage,
    evaluate_point,
    to_question,
)

STATE = "hello world jev"

# 真实小脑后端 fixture：conftest 的 make_backend（真实契约微型 bundle，约束 C1：禁 mock）


# ---------------------------------------------------------------------------
# DecisionPoint / TOML 文本构造 helper
# ---------------------------------------------------------------------------


def _point(**overrides) -> DecisionPoint:
    """合法 choice 决策点基准；overrides 覆盖字段以构造各合法/非法变体。"""
    fields: dict = {
        "id": "px",
        "stage": Stage.PRE,
        "question": "测试问题？",
        "tau": 0.5,
        "fallback_action": "a1",
        "question_type": "choice",
        "options": ("a1", "a2"),
    }
    fields.update(overrides)
    return DecisionPoint(**fields)


def _point_table(**overrides) -> str:
    """生成单个 [[point]] TOML 表文本；字段值 None 表示省略该字段。

    值均为已格式化的 TOML 字面量（字符串带引号、数组用 TOML 数组语法）。
    """
    fields = {
        "id": '"px"',
        "stage": '"PRE"',
        "question": '"测试问题？"',
        "tau": "0.5",
        "fallback_action": '"a1"',
        "question_type": '"choice"',
        "options": '["a1", "a2"]',
    }
    fields.update(overrides)
    lines = ["[[point]]"]
    for key, value in fields.items():
        if value is None:
            continue
        lines.append(f"{key} = {value}")
    return "\n".join(lines) + "\n\n"


VALID_TOML = (
    _point_table(id='"p_choice"', mode='"shadow"', options='["a1", "a2", "a3"]')
    + _point_table(
        id='"p_noul"',
        stage='"POST"',
        mode='"active"',
        tau="0.7",
        fallback_action='"yes"',
        question_type='"noul"',
        options=None,
    )
    + _point_table(
        id='"p_score"',
        stage='"MID"',
        mode='"off"',
        tau="0.5",
        fallback_action='"mid"',
        question_type='"score"',
        options='["low", "mid", "high"]',
    )
)


# ---------------------------------------------------------------------------
# 枚举与 dataclass 默认值
# ---------------------------------------------------------------------------


def test_stage_enum_members_are_pre_mid_post():
    assert [member.name for member in Stage] == ["PRE", "MID", "POST"]


def test_mode_enum_members_are_active_shadow_off():
    assert [member.name for member in Mode] == ["ACTIVE", "SHADOW", "OFF"]


def test_decision_point_defaults_to_active_mode_and_empty_options():
    point = _point(options=())  # 省略 mode/options 验证默认值（options=() 显式清空 helper 基准）
    assert point.mode is Mode.ACTIVE
    assert point.options == ()


def test_decision_point_is_frozen():
    point = _point()
    with pytest.raises(dataclasses.FrozenInstanceError):
        point.id = "changed"


def test_decision_defaults_from_fallback_false_and_reason_none():
    decision = Decision(
        point_id="px", stage=Stage.PRE, mode=Mode.ACTIVE, value="a1", confidence=0.8
    )
    assert decision.from_fallback is False
    assert decision.reason is None


# ---------------------------------------------------------------------------
# 注册表：register / get / ids / by_stage / active / shadow / set_mode
# ---------------------------------------------------------------------------


def test_register_and_get_roundtrip():
    registry = DecisionRegistry()
    point = _point()
    registry.register(point)
    assert registry.get("px") == point


def test_register_same_id_overrides_previous_point():
    registry = DecisionRegistry()
    registry.register(_point(question="第一版问题"))
    registry.register(_point(question="第二版问题", tau=0.9, mode=Mode.SHADOW))

    assert list(registry.ids) == ["px"]  # 覆盖不新增条目
    overridden = registry.get("px")
    assert overridden.question == "第二版问题"
    assert overridden.tau == pytest.approx(0.9)
    assert overridden.mode is Mode.SHADOW


def test_get_missing_point_raises_with_id_and_known_ids():
    registry = DecisionRegistry()
    registry.register(_point(id="known_a"))
    registry.register(_point(id="known_b"))

    with pytest.raises(JevProtocolError) as excinfo:
        registry.get("missing_point")
    message = str(excinfo.value)
    assert "missing_point" in message  # 错误消息含缺失 id
    assert "known_a" in message  # 也含已知清单
    assert "known_b" in message


def test_ids_preserves_declaration_order():
    registry = DecisionRegistry()
    for pid in ("pre_b", "mid_a", "pre_a", "post_a"):
        registry.register(_point(id=pid))
    assert list(registry.ids) == ["pre_b", "mid_a", "pre_a", "post_a"]


def test_by_stage_preserves_declaration_order():
    registry = DecisionRegistry()
    registry.register(_point(id="pre_b", stage=Stage.PRE))
    registry.register(_point(id="mid_a", stage=Stage.MID))
    registry.register(_point(id="pre_a", stage=Stage.PRE))
    registry.register(_point(id="post_a", stage=Stage.POST))

    assert [p.id for p in registry.by_stage(Stage.PRE)] == ["pre_b", "pre_a"]
    assert [p.id for p in registry.by_stage(Stage.MID)] == ["mid_a"]
    assert [p.id for p in registry.by_stage(Stage.POST)] == ["post_a"]


def test_active_and_shadow_filters_follow_mode_changes():
    registry = DecisionRegistry()
    registry.register(_point(id="p_active", stage=Stage.PRE))
    registry.register(_point(id="p_shadow", stage=Stage.PRE, mode=Mode.SHADOW))
    registry.register(_point(id="p_off", stage=Stage.PRE, mode=Mode.OFF))
    registry.register(_point(id="p_mid", stage=Stage.MID))

    assert [p.id for p in registry.active(Stage.PRE)] == ["p_active"]
    assert [p.id for p in registry.shadow(Stage.PRE)] == ["p_shadow"]

    registry.set_mode("p_active", Mode.SHADOW)
    assert [p.id for p in registry.active(Stage.PRE)] == []
    # 过滤器保持声明序：p_active 注册在先，切 shadow 后仍居前
    assert [p.id for p in registry.shadow(Stage.PRE)] == ["p_active", "p_shadow"]

    registry.set_mode("p_shadow", Mode.OFF)
    assert [p.id for p in registry.shadow(Stage.PRE)] == ["p_active"]


def test_set_mode_switches_point_mode_in_place():
    registry = DecisionRegistry()
    registry.register(_point())
    registry.set_mode("px", Mode.OFF)

    switched = registry.get("px")
    assert switched.mode is Mode.OFF
    assert switched.fallback_action == "a1"  # 其余字段不受切换影响


# ---------------------------------------------------------------------------
# register 校验矩阵（非法字段 → JevProtocolError）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"stage": "MIDDL"},
        {"mode": "paused"},
        {"question_type": "boolean"},
        {"tau": 0.0},
        {"tau": 1.5},
        {"options": ()},
        {"options": ("a1",), "fallback_action": "a1"},
        {"fallback_action": "not_listed"},
        {"question_type": "score", "options": (), "fallback_action": "mid"},
        {"question_type": "score", "options": ("low",), "fallback_action": "low"},
    ],
    ids=[
        "invalid_stage",
        "invalid_mode",
        "invalid_question_type",
        "tau_zero",
        "tau_above_one",
        "choice_without_options",
        "choice_single_option",
        "choice_fallback_not_in_options",
        "score_without_options",
        "score_single_option",
    ],
)
def test_register_rejects_invalid_point_fields(overrides):
    registry = DecisionRegistry()
    with pytest.raises(JevProtocolError):
        registry.register(_point(**overrides))


def test_register_accepts_tau_upper_boundary():
    registry = DecisionRegistry()
    registry.register(_point(tau=1.0))
    assert registry.get("px").tau == pytest.approx(1.0)


def test_register_noul_with_options_rejects_or_ignores_options():
    """契约允许两种行为：直接拒绝，或忽略 options 注册（保留则违规）。"""
    registry = DecisionRegistry()
    point = _point(question_type="noul", options=("a1", "a2"))
    try:
        registry.register(point)
    except JevProtocolError:
        return
    assert registry.get("px").options == ()


# ---------------------------------------------------------------------------
# from_toml：声明式配置加载与校验
# ---------------------------------------------------------------------------


def test_from_toml_loads_points_with_parsed_fields(tmp_path):
    path = tmp_path / "points.toml"
    path.write_text(VALID_TOML, encoding="utf-8")
    registry = DecisionRegistry.from_toml(path)

    assert list(registry.ids) == ["p_choice", "p_noul", "p_score"]

    choice = registry.get("p_choice")
    assert choice.stage is Stage.PRE
    assert choice.mode is Mode.SHADOW
    assert choice.tau == pytest.approx(0.5)
    assert choice.question == "测试问题？"
    assert choice.question_type == "choice"
    assert choice.options == ("a1", "a2", "a3")
    assert choice.fallback_action == "a1"

    noul = registry.get("p_noul")
    assert noul.stage is Stage.POST
    assert noul.mode is Mode.ACTIVE
    assert noul.question_type == "noul"
    assert noul.options == ()

    score = registry.get("p_score")
    assert score.stage is Stage.MID
    assert score.mode is Mode.OFF
    assert score.question_type == "score"
    assert score.options == ("low", "mid", "high")
    assert score.fallback_action == "mid"


@pytest.mark.parametrize(
    "overrides",
    [
        {"stage": '"MIDDL"'},
        {"mode": '"paused"'},
        {"question_type": '"boolean"'},
        {"tau": "0.0"},
        {"tau": "1.5"},
        {"options": None},
        {"options": '["only"]', "fallback_action": '"only"'},
        {"fallback_action": '"zz"'},
        {"question_type": '"score"', "options": None, "fallback_action": '"mid"'},
        {"question_type": '"score"', "options": '["low"]', "fallback_action": '"low"'},
    ],
    ids=[
        "invalid_stage",
        "invalid_mode",
        "invalid_question_type",
        "tau_zero",
        "tau_above_one",
        "choice_without_options",
        "choice_single_option",
        "choice_fallback_not_in_options",
        "score_without_options",
        "score_single_option",
    ],
)
def test_from_toml_rejects_invalid_point_definitions(tmp_path, overrides):
    path = tmp_path / "points.toml"
    path.write_text(_point_table(**overrides), encoding="utf-8")
    with pytest.raises(JevProtocolError):
        DecisionRegistry.from_toml(path)


def test_from_toml_rejects_duplicate_ids(tmp_path):
    body = _point_table(id='"dup"') + _point_table(id='"dup"', stage='"POST"')
    path = tmp_path / "points.toml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(JevProtocolError):
        DecisionRegistry.from_toml(path)


# ---------------------------------------------------------------------------
# to_question：决策点 → 小脑三型问题映射
# ---------------------------------------------------------------------------


def test_to_question_maps_choice_point():
    point = _point(options=("a1", "a2", "a3"))
    question = to_question(point)
    assert isinstance(question, ChoiceQuestion)
    # 选项渲染为纯标签（描述为 None，与 TypeSafe criteria {label: desc|null} 一致）
    assert question.choices == {"a1": None, "a2": None, "a3": None}
    assert question.instructions == point.question


def test_to_question_maps_score_point():
    point = _point(
        question_type="score", fallback_action="mid", options=("low", "mid", "high")
    )
    question = to_question(point)
    assert isinstance(question, ScoreQuestion)
    assert question.criteria == ["low", "mid", "high"]
    assert question.instructions == point.question


def test_to_question_maps_noul_point():
    point = _point(question_type="noul", options=(), fallback_action="safe")
    question = to_question(point)
    assert isinstance(question, NoulQuestion)
    assert question.instructions == point.question


# ---------------------------------------------------------------------------
# evaluate_point：真实 onnx 小脑评估（约束 C1：禁 mock）
# ---------------------------------------------------------------------------


def test_evaluate_point_choice_answer_with_real_onnx_backend(make_backend):
    point = _point(
        id="tiny_choice", stage=Stage.MID, mode=Mode.SHADOW, options=("a1", "a2", "a3")
    )
    decision = evaluate_point(make_backend(), point, STATE)

    assert isinstance(decision, Decision)
    assert decision.point_id == "tiny_choice"
    assert decision.stage is Stage.MID
    assert decision.mode is Mode.SHADOW
    assert decision.value in ("a1", "a2", "a3")
    assert 0.0 <= decision.confidence <= 1.0
    assert decision.from_fallback is False
    assert decision.reason is None


def test_evaluate_point_score_answer_with_real_onnx_backend(make_backend):
    point = _point(
        id="tiny_score",
        stage=Stage.POST,
        question_type="score",
        fallback_action="mid",
        options=("low", "mid", "high"),
    )
    decision = evaluate_point(make_backend(), point, STATE)

    assert decision.point_id == "tiny_score"
    assert decision.value in ("low", "mid", "high")
    assert 0.0 <= decision.confidence <= 1.0
    assert decision.from_fallback is False


def test_evaluate_point_noul_answer_with_real_onnx_backend(make_backend):
    point = _point(
        id="tiny_noul",
        stage=Stage.PRE,
        question_type="noul",
        fallback_action="safe",
        options=(),
    )
    decision = evaluate_point(make_backend(), point, STATE)

    assert decision.point_id == "tiny_noul"
    assert isinstance(decision.value, float)
    assert 0.0 <= decision.value <= 1.0
    assert 0.0 <= decision.confidence <= 1.0
    assert decision.from_fallback is False


# ---------------------------------------------------------------------------
# applied_value：tau 门控（安全侧回退）
# ---------------------------------------------------------------------------


def test_applied_value_falls_back_when_confidence_below_tau(make_backend):
    point = _point(id="gate_high", tau=0.99, options=("a1", "a2", "a3"))
    decision = evaluate_point(make_backend(), point, STATE)

    assert decision.confidence < point.tau  # 微型模型置信必低于 0.99
    assert decision.applied_value(point) == point.fallback_action


def test_applied_value_returns_value_when_confidence_meets_tau(make_backend):
    point = _point(id="gate_low", tau=0.01, options=("a1", "a2", "a3"))
    decision = evaluate_point(make_backend(), point, STATE)

    assert decision.confidence >= point.tau
    assert decision.applied_value(point) == decision.value


def test_applied_value_uses_value_at_exact_tau_boundary():
    point = _point(tau=0.5)
    decision = Decision(
        point_id="px", stage=Stage.PRE, mode=Mode.ACTIVE, value="a2", confidence=0.5
    )
    assert decision.applied_value(point) == "a2"
