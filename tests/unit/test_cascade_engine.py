"""T009 置信门控级联引擎（design.md §4.2 流程图 A 三路径判定；UT-02 纯映射）。

纯逻辑组件：输入 Decision 批、输出路由/动作判定，不涉及任何后端调用。
τ/点定义经真实内置注册表（load_builtin_registry）。
"""

import pytest
from jev.kernel.cascade.engine import CascadeEngine, MidAction, PostVerdict, Route

from jev.kernel.decisions.builtin import load_builtin_registry
from jev.kernel.decisions.framework import Decision, Mode, Stage

_REGISTRY = load_builtin_registry()

_POSTERIOR_KEYS = {"point_id", "value", "confidence", "applied", "from_fallback", "mode"}


@pytest.fixture
def engine() -> CascadeEngine:
    return CascadeEngine(load_builtin_registry())


def _d(point_id, value, confidence, mode=Mode.ACTIVE, from_fallback=False):
    """按真实注册表点定义构造 Decision（stage 取自注册表）。"""
    point = _REGISTRY.get(point_id)
    return Decision(
        point_id=point_id,
        stage=point.stage,
        mode=mode,
        value=value,
        confidence=confidence,
        from_fallback=from_fallback,
    )


def _pre(**overrides):
    """标准 5 点 active PRE 批（全简单高置信 → 基线 LIGHT）。

    单点覆盖传 (value, confidence)；传 None 表示批中缺失该点；
    传 Decision 整点替换（SHADOW / from_fallback 场景）。
    """
    defaults = {
        "needs_generation": ("generation", 0.9),
        "needs_reasoning": ("no_reasoning", 0.9),
        "intent": ("simple", 0.9),
        "complexity": ("low", 0.9),
        "safety": ("safe", 0.9),
    }
    batch = []
    for point_id, (value, confidence) in defaults.items():
        spec = overrides.get(point_id, (value, confidence))
        if spec is None:
            continue
        if isinstance(spec, Decision):
            batch.append(spec)
        else:
            batch.append(_d(point_id, spec[0], spec[1]))
    return batch


# ===== 三路径主判定 =====


def test_route_direct_on_high_confidence_decision_intent(engine):
    assert engine.route_chat(_pre(intent=("decision", 0.9))) is Route.DIRECT


def test_route_direct_at_intent_tau_boundary(engine):
    assert engine.route_chat(_pre(intent=("decision", 0.70))) is Route.DIRECT


def test_route_light_when_intent_below_tau_falls_back_complex(engine):
    assert engine.route_chat(_pre(intent=("decision", 0.5))) is Route.LIGHT


def test_route_light_on_all_simple_batch(engine):
    assert engine.route_chat(_pre()) is Route.LIGHT


def test_route_full_when_reasoning_needed(engine):
    assert engine.route_chat(_pre(needs_reasoning=("reasoning", 0.9))) is Route.FULL


def test_route_full_on_medium_complexity(engine):
    assert engine.route_chat(_pre(complexity=("medium", 0.9))) is Route.FULL


def test_route_full_when_complexity_below_tau_falls_back_high(engine):
    assert engine.route_chat(_pre(complexity=("low", 0.3))) is Route.FULL


def test_route_full_on_unsafe(engine):
    assert engine.route_chat(_pre(safety=("unsafe", 0.9))) is Route.FULL


def test_route_light_when_safety_below_tau_falls_back_safe(engine):
    assert engine.route_chat(_pre(safety=("safe", 0.5))) is Route.LIGHT


# ===== PRE 失败安全侧默认 =====


def test_route_full_when_any_active_decision_from_fallback(engine):
    batch = _pre(
        intent=("decision", 0.9),
        complexity=_d("complexity", "low", 0.9, from_fallback=True),
    )
    assert engine.route_chat(batch) is Route.FULL


# ===== SHADOW 决策不参与控制流 =====


def test_shadow_intent_not_routed_direct(engine):
    batch = _pre(intent=_d("intent", "decision", 0.9, mode=Mode.SHADOW))
    assert engine.route_chat(batch) is Route.LIGHT


def test_shadow_needs_retry_does_not_trigger_retry(engine):
    post = [_d("needs_retry", "retry", 0.9, mode=Mode.SHADOW)]
    assert engine.handle_post("答案", post) is PostVerdict.ACCEPT


# ===== 点缺失 / 注册表无该点 =====


def test_route_cannot_be_direct_when_intent_missing(engine):
    assert engine.route_chat(_pre(intent=None)) is Route.LIGHT


def test_route_skips_decision_for_unknown_point_without_error(engine):
    unknown = Decision(
        point_id="not_in_registry",
        stage=Stage.PRE,
        mode=Mode.ACTIVE,
        value="whatever",
        confidence=0.99,
    )
    assert engine.route_chat([*_pre(), unknown]) is Route.LIGHT


# ===== Route wire 值（X-Jev-Route 头小写） =====


def test_route_wire_values():
    assert Route.DIRECT.wire == "direct"
    assert Route.LIGHT.wire == "light"
    assert Route.FULL.wire == "full"


# ===== MID 早停 =====


def test_mid_stop_on_should_stop(engine):
    assert engine.handle_mid([_d("should_stop", "stop", 0.9)]) is MidAction.STOP


def test_mid_stop_on_off_track(engine):
    assert engine.handle_mid([_d("on_track", "off_track", 0.9)]) is MidAction.STOP


def test_mid_continue_when_should_stop_below_tau(engine):
    assert engine.handle_mid([_d("should_stop", "stop", 0.5)]) is MidAction.CONTINUE


def test_mid_continue_on_default_on_track_batch(engine):
    batch = [_d("on_track", "on_track", 0.9), _d("should_stop", "continue", 0.9)]
    assert engine.handle_mid(batch) is MidAction.CONTINUE


# ===== POST 采纳/重试/降级 =====


def test_post_retry_when_needs_retry_and_not_retried(engine):
    post = [_d("needs_retry", "retry", 0.9)]
    assert engine.handle_post("答案", post, retried=False) is PostVerdict.RETRY


def test_post_accept_when_retried_and_answer_complete(engine):
    post = [_d("needs_retry", "retry", 0.9), _d("answer_complete", "complete", 0.9)]
    assert engine.handle_post("答案", post, retried=True) is PostVerdict.ACCEPT


def test_post_downgrade_when_retried_and_incomplete(engine):
    post = [_d("answer_complete", "incomplete", 0.9)]
    assert engine.handle_post("答案", post, retried=True) is PostVerdict.DOWNGRADE


def test_post_downgrade_when_retried_and_off_topic(engine):
    post = [_d("matches_request", "off_topic", 0.9)]
    assert engine.handle_post("答案", post, retried=True) is PostVerdict.DOWNGRADE


def test_post_accept_when_needs_retry_below_tau(engine):
    post = [_d("needs_retry", "retry", 0.5)]
    assert engine.handle_post("答案", post) is PostVerdict.ACCEPT


def test_post_accept_with_arbitrary_answer_and_empty_batch(engine):
    assert engine.handle_post("任意回答文本 123", []) is PostVerdict.ACCEPT


# ===== 后验回流信号载体（FR12 蒸馏采集 / T012 台账） =====


def test_posterior_carries_route_and_six_key_decision_fields(engine):
    decisions = [
        _d("intent", "decision", 0.9),
        _d("complexity", "low", 0.3),
        _d("safety", "safe", 0.9, mode=Mode.SHADOW, from_fallback=True),
    ]
    signal = engine.posterior(Route.DIRECT, decisions)

    assert signal["route"] == "direct"
    entries = signal["decisions"]
    assert [entry["point_id"] for entry in entries] == ["intent", "complexity", "safety"]
    for entry in entries:
        assert set(entry) == _POSTERIOR_KEYS
    assert entries[0]["applied"] == "decision"
    assert entries[0]["value"] == "decision"
    assert entries[0]["confidence"] == 0.9
    # 低置信（0.3 < τ 0.60）→ applied 取 fallback_action
    assert entries[1]["value"] == "low"
    assert entries[1]["applied"] == "high"
    assert entries[2]["from_fallback"] is True
    assert entries[2]["mode"] == Mode.SHADOW.value
