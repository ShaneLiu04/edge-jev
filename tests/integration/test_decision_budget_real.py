"""AR011 真实 Laya 回归：置换预算 1 与完整预算的 applied 决策一致。

真实资产（Laya ONNX），无 mock。长 prompt 下单次 PRE 约数秒，标 slow。
"""

import json
from pathlib import Path

import pytest

from jev.bench.dataset import load_quality_set
from jev.bench.generation import build_shared_generation
from jev.bench.needles import build_needles
from jev.kernel.decisions.builtin import load_builtin_registry
from jev.kernel.decisions.evaluate import evaluate_stage
from jev.kernel.decisions.framework import Stage
from jev.kernel.decisions.probes import apply_permutation_budget

pytestmark = [pytest.mark.assets, pytest.mark.slow]

REPO = Path(__file__).resolve().parents[2]


def _applied(registry, decisions):
    return {
        decision.point_id: decision.applied_value(registry.get(decision.point_id))
        for decision in decisions
    }


def test_permutation_budget_matches_full_budget(real_kernel):
    registry = load_builtin_registry()
    points = registry.active(Stage.PRE)
    budgeted = apply_permutation_budget(points, 1)
    assert [p.permutations for p in budgeted] != [p.permutations for p in points]

    questions = (
        load_quality_set(REPO / "configs" / "quality-set.yaml")[:2]
        + build_needles(6)[:2]
        + build_shared_generation(2)
    )

    agree = 0
    total = 0
    for question in questions:
        state = json.dumps(
            [{"role": "user", "content": question.prompt}],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        full = _applied(
            registry,
            evaluate_stage(real_kernel.cerebellum, points, state).decisions,
        )
        clipped = _applied(
            registry,
            evaluate_stage(real_kernel.cerebellum, budgeted, state).decisions,
        )
        for point_id, value in full.items():
            total += 1
            if value == clipped[point_id]:
                agree += 1

    ratio = agree / total
    assert total > 0
    assert ratio >= 0.95, f"决策一致率不足: {agree}/{total}"
