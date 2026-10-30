"""AR014 真实 Laya：长上下文 int8 reasoning 门控与 RDMP 路由一致性。"""

import json
from pathlib import Path

import pytest

from jev.bench.prefixset import build_shared_prefix
from jev.kernel.cascade.engine import (
    CascadeEngine,
    Route,
    needs_minimal_confirmation,
)
from jev.kernel.cerebellum.laya_onnx import LayaOnnxBackend
from jev.kernel.decisions.builtin import load_builtin_registry
from jev.kernel.decisions.evaluate import evaluate_stage
from jev.kernel.decisions.framework import Stage, minimize_points

pytestmark = [pytest.mark.assets, pytest.mark.slow]

REPO = Path(__file__).resolve().parents[2]
INT8 = REPO / "models" / "laya" / "laya_int8.onnx"


def _int8_backend(real_kernel):
    if not INT8.is_file():
        pytest.skip("int8 模型未生成：先运行 uv run jev prepare-laya-int8")
    return LayaOnnxBackend(
        model_path=INT8,
        tokenizer_path=REPO / "models" / "laya" / "tokenizer" / "tokenizer.json",
        config_path=REPO / "models" / "laya" / "laya_config.json",
        providers=["CPUExecutionProvider"],
    )


def test_int8_reasoning_gate_matches_rdmp(real_kernel):
    registry = load_builtin_registry()
    engine = CascadeEngine(registry)
    points = registry.active(Stage.PRE)
    by_id = {point.id: point for point in points}
    wave1 = [by_id[name] for name in ("safety", "intent", "needs_reasoning")]
    minimal = minimize_points(wave1)
    wave2 = [by_id[name] for name in ("needs_generation", "complexity")]
    reasoning = by_id["needs_reasoning"]
    gate_point = minimize_points([reasoning])
    int8 = _int8_backend(real_kernel)
    fp32 = real_kernel.cerebellum

    for question in build_shared_prefix(6):
        state = json.dumps(
            [{"role": "user", "content": question.prompt}],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        assert fp32.count_tokens(state) > 4000

        first = evaluate_stage(fp32, minimal, state)
        short = engine.route_short_circuit(first.decisions)
        if needs_minimal_confirmation(short):
            first = evaluate_stage(fp32, wave1, state)
            short = engine.route_short_circuit(first.decisions)
        if short is None:
            second = evaluate_stage(fp32, wave2, state)
            merged = {d.point_id: d for d in first.decisions}
            merged.update({d.point_id: d for d in second.decisions})
            partial = [merged[p.id] for p in points if p.id in merged]
        else:
            partial = first.decisions
        route_rdmp = engine.route_chat(partial)

        gate = evaluate_stage(int8, gate_point, state).decisions[0]
        if gate.applied_value(reasoning) != "no_reasoning":
            route_int8 = Route.FULL
            assert len(gate_point) == 1
        else:
            route_int8 = route_rdmp

        assert route_int8 == route_rdmp, (question.id, route_rdmp, route_int8)


def test_agw_384_gate_matches_rdmp(real_kernel):
    """AR015?384 ????? shared ??????? RDMP ?????"""
    registry = load_builtin_registry()
    engine = CascadeEngine(registry)
    points = registry.active(Stage.PRE)
    by_id = {point.id: point for point in points}
    wave1 = [by_id[name] for name in ("safety", "intent", "needs_reasoning")]
    minimal = minimize_points(wave1)
    wave2 = [by_id[name] for name in ("needs_generation", "complexity")]
    reasoning = by_id["needs_reasoning"]
    gate_point = minimize_points([reasoning])
    if not INT8.is_file():
        pytest.skip("int8 ????????? uv run jev prepare-laya-int8")
    int8 = LayaOnnxBackend(
        model_path=INT8,
        tokenizer_path=REPO / "models" / "laya" / "tokenizer" / "tokenizer.json",
        config_path=REPO / "models" / "laya" / "laya_config.json",
        providers=["CPUExecutionProvider"],
        max_state_tokens=384,
    )
    fp32 = real_kernel.cerebellum
    hits = 0
    for question in build_shared_prefix(6):
        state = json.dumps(
            [{"role": "user", "content": question.prompt}],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        first = evaluate_stage(fp32, minimal, state)
        short = engine.route_short_circuit(first.decisions)
        if needs_minimal_confirmation(short):
            first = evaluate_stage(fp32, wave1, state)
            short = engine.route_short_circuit(first.decisions)
        if short is None:
            second = evaluate_stage(fp32, wave2, state)
            merged = {d.point_id: d for d in first.decisions}
            merged.update({d.point_id: d for d in second.decisions})
            partial = [merged[p.id] for p in points if p.id in merged]
        else:
            partial = first.decisions
        route_rdmp = engine.route_chat(partial)
        gate = evaluate_stage(int8, gate_point, state).decisions[0]
        if gate.applied_value(reasoning) != "no_reasoning":
            hits += 1
            assert route_rdmp is Route.FULL
    assert hits >= 4
