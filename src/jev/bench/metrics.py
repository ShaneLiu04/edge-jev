"""基准指标计算（design.md §4.4 bench/metrics.py，UT-09，FR13）。纯逻辑无后端。"""

import math


def percentile(values: list[float], p: float) -> float:
    """线性插值百分位（p∈[0,100]）；空列表 → nan。"""
    if not values:
        return math.nan
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * p / 100.0
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def latency_summary(values: list[float]) -> dict:
    """延迟分布：P50/P95/均值/极值/样本数（ms）。"""
    if not values:
        return {"n": 0, "p50": None, "p95": None, "mean": None, "min": None, "max": None}
    return {
        "n": len(values),
        "p50": round(percentile(values, 50), 3),
        "p95": round(percentile(values, 95), 3),
        "mean": round(sum(values) / len(values), 3),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
    }


def route_summary(routes: list[str | None]) -> dict:
    """路由分布与命中率：命中=direct+light（design.md §4.1 口径）。"""
    total = len(routes)
    counts = {"direct": 0, "light": 0, "full": 0, "failed": 0}
    for route in routes:
        if route in counts:
            counts[route] += 1
        else:
            counts["failed"] += 1
    hits = counts["direct"] + counts["light"]
    return {
        "total": total,
        "counts": counts,
        "hit_rate": hits / total if total else 0.0,
        "direct_rate": counts["direct"] / total if total else 0.0,
    }


def acceptance_rate(flags: list[bool | None]) -> float | None:
    """POST 复核通过率（None=不可复核，不计入；全不可复核 → None）。"""
    usable = [flag for flag in flags if flag is not None]
    if not usable:
        return None
    return sum(usable) / len(usable)


def precision_delta_pt(
    cascade: list[bool | None], baseline: list[bool | None]
) -> float | None:
    """精度保持：级联 vs 直连大脑的复核通过率差（百分点，成对可复核样本）。"""
    pairs = [
        (c, b) for c, b in zip(cascade, baseline, strict=True)
        if c is not None and b is not None
    ]
    if not pairs:
        return None
    cascade_rate = sum(c for c, _ in pairs) / len(pairs)
    baseline_rate = sum(b for _, b in pairs) / len(pairs)
    return (cascade_rate - baseline_rate) * 100.0


def cost_summary(
    cascade_tokens: list[int], baseline_tokens: list[int], routes: list[str | None]
) -> dict:
    """成本曲线：总/均 token、逐路径均值、节省比（级联 vs 直连大脑）。"""
    total_cascade = sum(cascade_tokens)
    total_baseline = sum(baseline_tokens)
    by_route: dict[str, list[int]] = {}
    for route, tokens in zip(routes, cascade_tokens, strict=True):
        if route is not None:
            by_route.setdefault(route, []).append(tokens)
    per_route = {
        route: round(sum(values) / len(values), 1)
        for route, values in sorted(by_route.items())
    }
    savings = (
        1.0 - total_cascade / total_baseline if total_baseline > 0 else None
    )
    return {
        "cascade_total_tokens": total_cascade,
        "baseline_total_tokens": total_baseline,
        "cascade_avg_tokens": round(total_cascade / len(cascade_tokens), 1)
        if cascade_tokens else 0.0,
        "baseline_avg_tokens": round(total_baseline / len(baseline_tokens), 1)
        if baseline_tokens else 0.0,
        "avg_tokens_by_route": per_route,
        "savings_ratio": round(savings, 4) if savings is not None else None,
    }


def bs05_gates(
    routes: list[str | None],
    cascade_latencies: list[float],
    baseline_latencies: list[float],
    *,
    hit_rate_threshold: float = 0.70,
    latency_ratio: float = 3.0,
) -> dict:
    """AR 验收门（BS-05）：命中率≥70%；直答 P50 ≤ 大脑路径 P50 / latency_ratio。

    大脑路径取 full 路由实际延迟；无 full 样本时退用直连大脑基线延迟。
    """
    routes_summary = route_summary(routes)
    direct_latencies = [
        latency for route, latency in zip(routes, cascade_latencies, strict=True)
        if route == "direct"
    ]
    full_latencies = [
        latency for route, latency in zip(routes, cascade_latencies, strict=True)
        if route == "full"
    ]
    brain_path = full_latencies if full_latencies else baseline_latencies
    direct_p50 = percentile(direct_latencies, 50) if direct_latencies else None
    brain_p50 = percentile(brain_path, 50) if brain_path else None
    hit_pass = routes_summary["hit_rate"] >= hit_rate_threshold
    latency_pass = (
        direct_p50 is not None
        and brain_p50 is not None
        and direct_p50 <= brain_p50 / latency_ratio
    )
    return {
        "hit_rate": round(routes_summary["hit_rate"], 4),
        "hit_rate_pass": hit_pass,
        "direct_p50_ms": round(direct_p50, 3) if direct_p50 is not None else None,
        "brain_path_p50_ms": round(brain_p50, 3) if brain_p50 is not None else None,
        "latency_pass": latency_pass,
        "pass": hit_pass and latency_pass,
    }


__all__ = [
    "percentile",
    "latency_summary",
    "route_summary",
    "acceptance_rate",
    "precision_delta_pt",
    "cost_summary",
    "bs05_gates",
]
