"""AR010 返工：POST 复核必须看到答案尾部（真实 Laya bundle）。

回归目标：长 state（shared_prefix ~2.6k tokens）超 max_len=512 时，旧实现保留头部，
assistant 答案被截掉，POST 对所有答案给出相同判定（恒定 needs_retry）。改为保尾部后，
正确长答与空答必须至少在一个 POST active 决策上出现可区分结果。
"""

import json

import pytest

from jev.bench.prefixset import build_shared_prefix
from jev.kernel.decisions.evaluate import evaluate_stage
from jev.kernel.decisions.framework import Stage

pytestmark = pytest.mark.assets

PASSING_ANSWER = "答案 K-1000 的巡检编号是 4815。"
EMPTY_ANSWER = ""


def _applied(kernel, decisions) -> dict[str, str]:
    result: dict[str, str] = {}
    for decision in decisions:
        point = kernel.registry.get(decision.point_id)
        result[decision.point_id] = decision.applied_value(point)
    return result


def test_long_state_post_review_sees_answer_tail(real_kernel):
    question = build_shared_prefix(1)[0]
    messages = [{"role": "user", "content": question.prompt}]
    assert len(real_kernel.cerebellum._encode(json.dumps(messages))) > 512

    points = real_kernel.registry.active(Stage.POST)
    assert points

    passing_state = json.dumps(
        messages + [{"role": "assistant", "content": PASSING_ANSWER}],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    empty_state = json.dumps(
        messages + [{"role": "assistant", "content": EMPTY_ANSWER}],
        ensure_ascii=False,
        separators=(",", ":"),
    )

    passing = _applied(
        real_kernel,
        evaluate_stage(real_kernel.cerebellum, points, passing_state).decisions,
    )
    empty = _applied(
        real_kernel,
        evaluate_stage(real_kernel.cerebellum, points, empty_state).decisions,
    )

    assert passing != empty
