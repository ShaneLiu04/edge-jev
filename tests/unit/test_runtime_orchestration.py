"""AR005 T004/T006：缓存准入与 JCBA 编排接线（FR5-2/FR5-3）。"""

from jev.kernel.cache.semantic import CacheConfig, SemanticCache
from jev.kernel.cascade.draft import DraftConfig
from jev.kernel.decisions.builtin import load_builtin_registry
from jev.kernel.decisions.framework import Decision, Mode
from jev.kernel.runtime.conformal import AdmissionPolicy
from jev.kernel.runtime.prompt import PromptConfig, build_prompt

_REGISTRY = load_builtin_registry()


class _Vectors:
    def __init__(self, mapping):
        self._mapping = mapping

    def embed(self, texts):
        return [self._mapping[text] for text in texts]


def _decision(point_id: str, value: str, confidence: float = 0.9) -> Decision:
    point = _REGISTRY.get(point_id)
    return Decision(
        point_id=point_id,
        stage=point.stage,
        mode=Mode.ACTIVE,
        value=value,
        confidence=confidence,
    )


def test_cache_conformal_admission_marks_and_blocks_negative():
    provider = _Vectors(
        {
            "原始问题": [1.0, 0.0, 0.0],
            "复述问题": [0.99, 0.01, 0.0],
            "无关问题": [0.0, 1.0, 0.0],
            "查询复述": [0.995, 0.005, 0.0],
        }
    )
    cache = SemanticCache(CacheConfig(enabled=True, similarity=0.5, margin=0.0))
    cache.store("fp", "原始问题", "答案", "light", embeddings=provider)
    policy = AdmissionPolicy(True, 0.98, 0.1, 0.0, 1.0, 20)
    hit = cache.lookup("fp", "查询复述", embeddings=provider, admission=policy)
    assert hit is not None
    assert hit.admission == "conformal"
    assert hit.threshold == 0.98
    miss = cache.lookup("fp", "无关问题", embeddings=provider, admission=policy)
    assert miss is None


def test_cache_without_admission_keeps_fixed_threshold():
    provider = _Vectors({"q": [1.0, 0.0], "p": [0.8, 0.6]})
    cache = SemanticCache(CacheConfig(enabled=True, similarity=0.7, margin=0.0))
    cache.store("fp", "q", "a", "light", embeddings=provider)
    hit = cache.lookup("fp", "p", embeddings=provider)
    assert hit is not None
    assert hit.admission == "similarity"
    assert hit.threshold == 0.7


def _long_prompt(turns: int = 6) -> list[dict]:
    messages = [{"role": "system", "content": "系统提示"}]
    for index in range(turns):
        messages.append({"role": "user", "content": f"问题 {index}：" + "背景" * 200})
        messages.append({"role": "assistant", "content": f"回答 {index}：" + "说明" * 200})
    return messages


def test_allocator_composes_draft_memory_and_blocks_deterministically():
    messages = _long_prompt()
    config = PromptConfig(
        memory_enabled=True,
        draft_enabled=True,
        keep_turns=2,
        allocator_enabled=True,
        allocator_budget_ratio=0.5,
        allocator_memory_benefit=0.99,
    )
    decisions = [_decision("intent", "simple")]
    first = build_prompt(
        messages, decisions, config, registry=_REGISTRY, query="问题 5 背景"
    )
    second = build_prompt(
        messages, decisions, config, registry=_REGISTRY, query="问题 5 背景"
    )
    assert first.messages == second.messages
    assert first.allocation is not None
    assert first.allocation.skipped is None
    assert first.after_tokens < first.before_tokens
    assert first.messages[0]["content"] == first.draft_prefix
    assert len(first.messages) >= 2


def test_allocator_short_prompt_falls_back_without_rewrite():
    messages = [{"role": "user", "content": "短问题"}]
    config = PromptConfig(allocator_enabled=True, allocator_min_prompt_tokens=100)
    built = build_prompt(messages, [_decision("intent", "simple")], config)
    assert built.allocation is not None
    assert built.allocation.skipped == "short"
    assert built.messages == messages


def test_allocator_disabled_is_bitwise_legacy():
    messages = _long_prompt(2)
    decisions = [_decision("intent", "simple", 0.9)]
    config = PromptConfig(draft_enabled=True)
    built = build_prompt(messages, decisions, config, registry=_REGISTRY)
    legacy = build_prompt(messages, decisions, PromptConfig(draft_enabled=True), registry=_REGISTRY)
    assert built.messages == legacy.messages
    assert built.allocation is None


def test_draft_config_unused_import_sanity():
    assert DraftConfig().enabled is False


def test_risk_gate_forces_full_when_denied():
    from jev.kernel.cascade.engine import CascadeEngine, Route, RouteDecision
    from jev.kernel.decisions.builtin import load_builtin_registry
    from jev.kernel.runtime.conformal import CalibrationResult, RiskController
    from jev.server.chat import _CascadeContext

    registry = load_builtin_registry()
    controller = RiskController(
        CalibrationResult(True, 0.3, 0.5, 0.0, 0.05, 30, 0.1, "le")
    )
    context = _CascadeContext(
        None,
        None,
        registry,
        CascadeEngine(registry),
        None,
        None,
        "req-risk",
        [{"role": "user", "content": "hi"}],
        0.0,
        16,
        None,
        risk_controller=controller,
    )
    allowed = context._apply_risk_gate(
        RouteDecision(Route.LIGHT, "confident", False), 0.2
    )
    assert allowed.route is Route.LIGHT
    assert context.last_runtime["risk"]["decision"] == "allow"
    denied = context._apply_risk_gate(
        RouteDecision(Route.LIGHT, "borderline", True), 0.9
    )
    assert denied.route is Route.FULL
    assert denied.skipped_reason == "risk"
    assert context.last_runtime["risk"]["decision"] == "deny"
    full = context._apply_risk_gate(RouteDecision(Route.FULL, "uncertain", False), 0.9)
    assert full.route is Route.FULL
    assert full.skipped_reason is None
