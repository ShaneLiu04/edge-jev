"""AR012 真实 Laya：分阶段路由与完整 PRE 路由一致。

真实资产，无 mock。仅验证路由语义，不启动大脑。
"""

import json
from pathlib import Path

import pytest

from jev.bench.dataset import load_quality_set
from jev.bench.generation import build_shared_generation
from jev.bench.needles import build_needles
from jev.kernel.cascade.engine import CascadeEngine
from jev.kernel.decisions.builtin import load_builtin_registry
from jev.kernel.decisions.evaluate import evaluate_stage
from jev.kernel.decisions.framework import Stage
from jev.kernel.decisions.probes import plan_questions

pytestmark = [pytest.mark.assets, pytest.mark.slow]

REPO = Path(__file__).resolve().parents[2]


def test_staged_route_matches_full_route(real_kernel):
    registry = load_builtin_registry()
    engine = CascadeEngine(registry)
    points = registry.active(Stage.PRE)
    by_id = {point.id: point for point in points}
    wave1 = [by_id[name] for name in ("safety", "intent", "needs_reasoning")]
    wave2 = [by_id[name] for name in ("needs_generation", "complexity")]

    all_qids, _ = plan_questions(points)
    wave1_qids, _ = plan_questions(wave1)
    assert len(wave1_qids) < len(all_qids)

    questions = (
        load_quality_set(REPO / "configs" / "quality-set.yaml")[:2]
        + build_needles(6)[:2]
        + build_shared_generation(2)
    )

    for question in questions:
        state = json.dumps(
            [{"role": "user", "content": question.prompt}],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        full = evaluate_stage(real_kernel.cerebellum, points, state)
        route_full = engine.route_chat(full.decisions)

        first = evaluate_stage(real_kernel.cerebellum, wave1, state)
        short = engine.route_short_circuit(first.decisions)
        if short is None:
            second = evaluate_stage(real_kernel.cerebellum, wave2, state)
            merged = {d.point_id: d for d in first.decisions}
            merged.update({d.point_id: d for d in second.decisions})
            partial = [merged[p.id] for p in points if p.id in merged]
        else:
            partial = first.decisions
        route_staged = engine.route_chat(partial)
        assert route_staged == route_full, (
            question.id,
            route_full,
            route_staged,
            short,
        )
