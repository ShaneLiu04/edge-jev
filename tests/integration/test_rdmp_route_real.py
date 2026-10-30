"""AR013 真实 Laya：最小 wave1 + 保守确认的路由一致性。"""

import json
from pathlib import Path

import pytest

from jev.bench.dataset import load_quality_set
from jev.bench.generation import build_shared_generation
from jev.bench.needles import build_needles
from jev.bench.prefixset import build_shared_prefix
from jev.kernel.cascade.engine import (
    CascadeEngine,
    Route,
    needs_minimal_confirmation,
)
from jev.kernel.decisions.builtin import load_builtin_registry
from jev.kernel.decisions.evaluate import evaluate_stage
from jev.kernel.decisions.framework import Stage, minimize_points
from jev.kernel.decisions.probes import plan_questions

pytestmark = [pytest.mark.assets, pytest.mark.slow]

REPO = Path(__file__).resolve().parents[2]
_RANK = {Route.DIRECT: 0, Route.LIGHT: 1, Route.FULL: 2}


def test_minimal_wave1_never_aggressive(real_kernel):
    registry = load_builtin_registry()
    engine = CascadeEngine(registry)
    by_id = {point.id: point for point in registry.active(Stage.PRE)}
    wave1 = [by_id[name] for name in ("safety", "intent", "needs_reasoning")]
    wave2 = [by_id[name] for name in ("needs_generation", "complexity")]
    minimal = minimize_points(wave1)

    min_qids, _ = plan_questions(minimal)
    full_wave1_qids, _ = plan_questions(wave1)
    assert len(min_qids) == 3
    assert len(min_qids) < len(full_wave1_qids)

    questions = (
        load_quality_set(REPO / "configs" / "quality-set.yaml")[:2]
        + build_needles(6)[:2]
        + build_shared_generation(2)
        + build_shared_prefix(2)
    )

    conservative = 0
    confirmed = 0
    for question in questions:
        state = json.dumps(
            [{"role": "user", "content": question.prompt}],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        full = evaluate_stage(real_kernel.cerebellum, registry.active(Stage.PRE), state)
        route_full = engine.route_chat(full.decisions)

        first = evaluate_stage(real_kernel.cerebellum, minimal, state)
        short = engine.route_short_circuit(first.decisions)
        if needs_minimal_confirmation(short):
            confirmed += 1
            first = evaluate_stage(real_kernel.cerebellum, wave1, state)
            short = engine.route_short_circuit(first.decisions)
        if short is None:
            second = evaluate_stage(real_kernel.cerebellum, wave2, state)
            merged = {d.point_id: d for d in first.decisions}
            merged.update({d.point_id: d for d in second.decisions})
            partial = [merged[p.id] for p in registry.active(Stage.PRE) if p.id in merged]
        else:
            partial = first.decisions
        route_min = engine.route_chat(partial)

        if route_min != route_full:
            assert _RANK[route_min] > _RANK[route_full], (
                question.id,
                route_full,
                route_min,
                "aggressive",
            )
            conservative += 1

    assert len(questions) == 8
    assert confirmed >= 1
    assert conservative <= 2
