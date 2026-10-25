"""基准指标计算（design.md §4.4 bench/metrics.py，UT-09，FR13）。纯逻辑无后端。"""

import hashlib
import math
import platform
import random
import sys
from pathlib import Path

BOOTSTRAP_DEFAULT_RESAMPLES = 2000
BOOTSTRAP_DEFAULT_SEED = 20261025


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


def paired_deltas(
    cascade: list[bool | None], baseline: list[bool | None]
) -> list[int]:
    """成对差值（仅可复核样本）：cascade-baseline ∈ {-1, 0, 1}。"""
    return [
        int(c) - int(b)
        for c, b in zip(cascade, baseline, strict=True)
        if c is not None and b is not None
    ]


def order_flip_rate(
    single_order: list[str | None], fused: list[str | None]
) -> dict:
    """顺序翻转率：单顺序 vs 置换融合的 applied 值不一致比例（AR002 FR1-2）。"""
    pairs = [
        (a, b) for a, b in zip(single_order, fused, strict=True)
        if a is not None and b is not None
    ]
    if not pairs:
        return {"n": 0, "flips": 0, "rate": None}
    flips = sum(1 for a, b in pairs if a != b)
    return {"n": len(pairs), "flips": flips, "rate": round(flips / len(pairs), 4)}


def paired_bootstrap_ci(
    deltas: list[float],
    *,
    resamples: int = BOOTSTRAP_DEFAULT_RESAMPLES,
    seed: int = BOOTSTRAP_DEFAULT_SEED,
    confidence: float = 0.95,
) -> dict:
    """成对 bootstrap 置信区间（AR002 FR4-2）：逐样本差值重采样取分位。

    纯 numpy 之外只用标准库 random（固定种子可复现），复杂度 O(resamples·n)。
    """
    if not deltas:
        return {"n": 0, "mean": None, "low": None, "high": None,
                "confidence": confidence, "resamples": resamples}
    if resamples < 1:
        raise ValueError("resamples 必须 ≥ 1")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence 必须在 (0, 1) 内")
    rng = random.Random(seed)
    count = len(deltas)
    means: list[float] = []
    for _ in range(resamples):
        total = 0.0
        for _ in range(count):
            total += deltas[rng.randrange(count)]
        means.append(total / count)
    tail = (1.0 - confidence) / 2.0
    return {
        "n": count,
        "mean": round(sum(deltas) / count, 6),
        "low": round(percentile(means, tail * 100.0), 6),
        "high": round(percentile(means, (1.0 - tail) * 100.0), 6),
        "confidence": confidence,
        "resamples": resamples,
    }


def environment_fingerprint(
    *,
    extra: dict | None = None,
    model_hashes: dict[str, str] | None = None,
) -> dict:
    """环境指纹（AR002 FR4-2）：解释器/依赖/平台/资产哈希，供报告可复现追溯。"""
    versions: dict[str, str] = {"python": sys.version.split()[0]}
    for module_name in ("onnxruntime", "httpx", "numpy", "yaml"):
        try:
            module = __import__(module_name)
        except ImportError:
            versions[module_name] = "absent"
        else:
            versions[module_name] = str(getattr(module, "__version__", "unknown"))
    fingerprint = {
        "python": versions.pop("python"),
        "versions": versions,
        "platform": platform.platform(),
        "machine": platform.machine(),
    }
    if model_hashes:
        fingerprint["model_hashes"] = {
            Path(name).name: digest for name, digest in sorted(model_hashes.items())
        }
    if extra:
        fingerprint["extra"] = dict(sorted(extra.items()))
    return fingerprint


def file_sha256(path: str | Path, *, chunk_size: int = 1 << 20) -> str:
    """流式文件哈希（资产指纹；不加载整文件进内存）。"""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "percentile",
    "latency_summary",
    "route_summary",
    "acceptance_rate",
    "precision_delta_pt",
    "cost_summary",
    "bs05_gates",
    "order_flip_rate",
    "paired_deltas",
    "paired_bootstrap_ci",
    "environment_fingerprint",
    "file_sha256",
]
