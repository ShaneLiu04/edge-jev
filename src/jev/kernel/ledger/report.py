"""shadow 决策对比报告（design.md BS-03 口径，srs FR4/FR8）。

反事实口径：将台账中每请求的 shadow 决策提升为 active 后重放级联判定
（route/MID/POST），与该请求实际发生的控制流对比——统计「shadow 若当时
生效，控制流是否会改变」。from_fallback 的 shadow 决策不可信，不参与
提升（计入 fallback 统计）。
"""

import dataclasses
from collections.abc import Iterable

from jev.kernel.cascade.engine import CascadeEngine
from jev.kernel.decisions.framework import Decision, Mode, Stage
from jev.kernel.ledger.writer import LedgerEntry


def shadow_report(entries: Iterable[LedgerEntry], engine: CascadeEngine) -> dict:
    """从台账条目生成 shadow 对比报告（跨请求聚合）。

    返回结构：
    - requests：参与统计的请求数（含 decision 条目者）
    - shadow_points：逐点统计（评估次数/fallback 次数/applied 值分布/平均置信）
    - flow_diff：反事实差异（route_changed/mid_would_change/post_would_change，
      值为差异命中的 request_id 列表）
    """
    decisions_by_request: dict[str, list[LedgerEntry]] = {}
    routes: dict[str, str] = {}
    for entry in entries:
        if entry.kind == "decision":
            decisions_by_request.setdefault(entry.request_id, []).append(entry)
        elif entry.kind == "route" and entry.route:
            routes[entry.request_id] = entry.route

    point_stats: dict[str, dict] = {}
    flow_diff: dict[str, list[str]] = {
        "route_changed": [],
        "mid_would_change": [],
        "post_would_change": [],
    }

    for request_id, decision_entries in decisions_by_request.items():
        active: dict[str, list[Decision]] = {"PRE": [], "MID": [], "POST": []}
        shadow: dict[str, list[Decision]] = {"PRE": [], "MID": [], "POST": []}
        for entry in decision_entries:
            decision = _to_decision(entry)
            if entry.mode == Mode.SHADOW.value:
                shadow[entry.stage].append(decision)
                _bump(point_stats, entry)
            else:
                active[entry.stage].append(decision)

        actual_route = routes.get(request_id)
        if actual_route is not None:
            counterfactual_pre = active["PRE"] + _promote(shadow["PRE"])
            if engine.route_chat(counterfactual_pre).wire != actual_route:
                flow_diff["route_changed"].append(request_id)

        if active["MID"] or shadow["MID"]:
            actual_action = engine.handle_mid(active["MID"])
            counterfactual_action = engine.handle_mid(
                active["MID"] + _promote(shadow["MID"])
            )
            if actual_action is not counterfactual_action:
                flow_diff["mid_would_change"].append(request_id)

        if active["POST"] or shadow["POST"]:
            actual_verdict = engine.handle_post("", active["POST"], retried=False)
            counterfactual_verdict = engine.handle_post(
                "", active["POST"] + _promote(shadow["POST"]), retried=False
            )
            if actual_verdict is not counterfactual_verdict:
                flow_diff["post_would_change"].append(request_id)

    for stats in point_stats.values():
        confident = stats.pop("_evaluated_confident")
        stats["confidence_avg"] = (
            round(stats.pop("_confidence_sum") / confident, 4) if confident else None
        )

    return {
        "requests": len(decisions_by_request),
        "shadow_points": point_stats,
        "flow_diff": flow_diff,
    }


def _to_decision(entry: LedgerEntry) -> Decision:
    return Decision(
        point_id=entry.point_id or "",
        stage=Stage(entry.stage),
        mode=Mode(entry.mode),
        value=entry.value,
        confidence=float(entry.confidence or 0.0),
        from_fallback=bool(entry.from_fallback),
    )


def _promote(decisions: list[Decision]) -> list[Decision]:
    """shadow → active（跳过 from_fallback：不可信决策不参与反事实）。"""
    return [
        dataclasses.replace(decision, mode=Mode.ACTIVE)
        for decision in decisions
        if not decision.from_fallback
    ]


def _bump(point_stats: dict, entry: LedgerEntry) -> None:
    stats = point_stats.setdefault(
        entry.point_id,
        {
            "evaluated": 0,
            "fallback": 0,
            "values": {},
            "_confidence_sum": 0.0,
            "_evaluated_confident": 0,
        },
    )
    stats["evaluated"] += 1
    if entry.from_fallback:
        stats["fallback"] += 1
        return
    key = str(entry.applied if entry.applied is not None else entry.value)
    stats["values"][key] = stats["values"].get(key, 0) + 1
    stats["_confidence_sum"] += float(entry.confidence or 0.0)
    stats["_evaluated_confident"] += 1
