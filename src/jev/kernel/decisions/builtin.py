"""18 内置决策点（DuoMind 清单 PRE9/MID4/POST5，srs.md §3.4 FR4 点名）。

与 configs/decision-points.toml 同名同序同字段——同步由
tests/unit/test_decisions_builtin.py::test_toml_registry_matches_builtin_registry
持续保证（两处任一变更未同步即测试失败）。
"""

from jev.kernel.decisions.framework import (
    DecisionPoint,
    DecisionRegistry,
    Mode,
    Stage,
)

BUILTIN_POINTS: tuple[DecisionPoint, ...] = (
    # ===== PRE（请求前，9）=====
    DecisionPoint(
        id="needs_generation",
        stage=Stage.PRE,
        question="该请求是否需要生成新文本（而非检索/改写/判断）？",
        tau=0.60,
        fallback_action="generation",
        question_type="choice",
        options=("generation", "no_generation"),
    ),
    DecisionPoint(
        id="needs_reasoning",
        stage=Stage.PRE,
        question="该请求是否需要多步推理？",
        tau=0.60,
        fallback_action="reasoning",
        question_type="choice",
        options=("reasoning", "no_reasoning"),
    ),
    DecisionPoint(
        id="intent",
        stage=Stage.PRE,
        question="该请求的意图属于哪类：决策类/简单生成/复杂推理？",
        tau=0.70,
        fallback_action="complex",
        question_type="choice",
        options=("decision", "simple", "complex"),
    ),
    DecisionPoint(
        id="complexity",
        stage=Stage.PRE,
        question="该请求的复杂程度（低/中/高）？",
        tau=0.60,
        fallback_action="high",
        question_type="score",
        options=("low", "medium", "high"),
    ),
    DecisionPoint(
        id="ambiguity",
        stage=Stage.PRE,
        question="该请求是否存在歧义需要澄清？",
        tau=0.60,
        fallback_action="no_ambiguity",
        question_type="choice",
        options=("ambiguous", "no_ambiguity"),
        mode=Mode.SHADOW,
    ),
    DecisionPoint(
        id="safety",
        stage=Stage.PRE,
        question="该请求是否包含不安全内容？",
        tau=0.80,
        fallback_action="safe",
        question_type="choice",
        options=("unsafe", "safe"),
    ),
    DecisionPoint(
        id="verbosity",
        stage=Stage.PRE,
        question="期望回答的详尽程度（简洁/标准/详尽）？",
        tau=0.50,
        fallback_action="standard",
        question_type="choice",
        options=("concise", "standard", "verbose"),
        mode=Mode.SHADOW,
    ),
    DecisionPoint(
        id="format",
        stage=Stage.PRE,
        question="期望回答的格式（文本/列表/表格/代码）？",
        tau=0.50,
        fallback_action="text",
        question_type="choice",
        options=("text", "list", "table", "code"),
        mode=Mode.SHADOW,
    ),
    DecisionPoint(
        id="needs_tool",
        stage=Stage.PRE,
        question="该请求是否需要调用外部工具？",
        tau=0.70,
        fallback_action="no_tool",
        question_type="choice",
        options=("tool", "no_tool"),
        mode=Mode.SHADOW,
    ),
    # ===== MID（分段生成中，4）=====
    DecisionPoint(
        id="on_track",
        stage=Stage.MID,
        question="当前生成内容是否仍然切合原始请求？",
        tau=0.60,
        fallback_action="on_track",
        question_type="choice",
        options=("on_track", "off_track"),
    ),
    DecisionPoint(
        id="step_complete",
        stage=Stage.MID,
        question="当前段落是否已完成可进入下一段？",
        tau=0.60,
        fallback_action="complete",
        question_type="choice",
        options=("complete", "incomplete"),
    ),
    DecisionPoint(
        id="should_stop",
        stage=Stage.MID,
        question="是否应该现在停止生成？",
        tau=0.70,
        fallback_action="continue",
        question_type="choice",
        options=("stop", "continue"),
    ),
    DecisionPoint(
        id="confidence_mid",
        stage=Stage.MID,
        question="当前生成内容的质量置信度（0-1）？",
        tau=0.50,
        fallback_action="low",
        question_type="score",
        options=("low", "medium", "high"),
        mode=Mode.SHADOW,
    ),
    # ===== POST（生成后，5）=====
    DecisionPoint(
        id="answer_complete",
        stage=Stage.POST,
        question="回答是否完整回答了请求？",
        tau=0.70,
        fallback_action="complete",
        question_type="choice",
        options=("complete", "incomplete"),
    ),
    DecisionPoint(
        id="matches_request",
        stage=Stage.POST,
        question="回答是否切合请求（未跑题）？",
        tau=0.70,
        fallback_action="matches",
        question_type="choice",
        options=("matches", "off_topic"),
    ),
    DecisionPoint(
        id="needs_retry",
        stage=Stage.POST,
        question="是否需要重新生成？",
        tau=0.60,
        fallback_action="no_retry",
        question_type="choice",
        options=("retry", "no_retry"),
    ),
    DecisionPoint(
        id="too_verbose",
        stage=Stage.POST,
        question="回答是否过于冗长？",
        tau=0.60,
        fallback_action="not_verbose",
        question_type="choice",
        options=("verbose", "not_verbose"),
        mode=Mode.SHADOW,
    ),
    DecisionPoint(
        id="correct_format",
        stage=Stage.POST,
        question="回答格式是否正确符合期望？",
        tau=0.60,
        fallback_action="correct",
        question_type="choice",
        options=("correct", "incorrect"),
    ),
)


def load_builtin_registry() -> DecisionRegistry:
    registry = DecisionRegistry()
    for point in BUILTIN_POINTS:
        registry.register(point)
    return registry
