"""基准报告输出（design.md §4.4 bench/report.py，FR13）：JSON + Markdown。"""

import json
import time
from pathlib import Path

from jev.bench.runner import BenchResult


def render_markdown(result: BenchResult) -> str:
    """五类指标结构化 Markdown（延迟/命中率/精度保持/成本/决策点开销 + 验收门）。"""
    routes = result.routes
    precision = result.precision
    cost = result.cost
    gates = result.gates
    overhead = result.decision_overhead
    lines: list[str] = [
        "# Jev 基准报告（jev bench）",
        "",
        f"- 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 题目数: {routes['total']}",
        "",
        "## 路由分布与命中率",
        "",
        "| direct | light | full | failed | 命中率 | 直答率 |",
        "|---|---|---|---|---|---|",
        f"| {routes['counts']['direct']} | {routes['counts']['light']} "
        f"| {routes['counts']['full']} | {routes['counts']['failed']} "
        f"| {routes['hit_rate']:.1%} | {routes['direct_rate']:.1%} |",
        "",
        "## 延迟分布（ms）",
        "",
        "| 路径 | P50 | P95 | 均值 | n |",
        "|---|---|---|---|---|",
    ]
    rows = [("级联全程", result.latency["cascade"])]
    if result.latency["baseline_brain"]["n"]:
        rows.append(("直连大脑基线", result.latency["baseline_brain"]))
    rows.extend(
        (f"级联·{route}", summary)
        for route, summary in result.latency["by_route"].items()
    )
    for name, summary in rows:
        if summary["n"]:
            lines.append(
                f"| {name} | {summary['p50']} | {summary['p95']} "
                f"| {summary['mean']} | {summary['n']} |"
            )
    lines.extend(
        [
            "",
            "## 精度保持（POST 复核通过率，同一把尺子）",
            "",
            "| 级联 | 直连大脑 | 差值（pt） |",
            "|---|---|---|",
            f"| {_fmt_rate(precision['cascade_acceptance'])} "
            f"| {_fmt_rate(precision['baseline_acceptance'])} "
            f"| {_fmt_delta(precision['delta_pt'])} |",
            "",
            "## 成本（token）",
            "",
            "| 口径 | 总量 | 均值/题 | 节省比 |",
            "|---|---|---|---|",
            f"| 级联 | {cost['cascade_total_tokens']} "
            f"| {cost['cascade_avg_tokens']} | "
            f"{_fmt_ratio(cost['savings_ratio'])} |",
            f"| 直连大脑 | {cost['baseline_total_tokens']} "
            f"| {cost['baseline_avg_tokens']} | — |",
            "",
            "逐路径均 token: "
            + ", ".join(
                f"{route}={tokens}"
                for route, tokens in cost["avg_tokens_by_route"].items()
            )
            or "（无）",
            "",
            "## 决策点开销",
            "",
            f"- PRE 批量前向（{overhead.get('batch_questions', 0)} 问/批）: "
            f"P50={overhead.get('p50')}ms / P95={overhead.get('p95')}ms"
            f"（n={overhead.get('n', 0)}）",
            "- 决策面: "
            + ", ".join(
                f"{stage}[active={counts['active']},shadow={counts['shadow']}]"
                for stage, counts in result.stage_counts.items()
            ),
            "",
            "## BS-05 验收门",
            "",
            f"- 命中率 ≥70%: {'PASS' if gates['hit_rate_pass'] else 'FAIL'}"
            f"（实际 {gates['hit_rate']:.1%}）",
            f"- 直答 P50 ≤ 大脑路径 P50×1/3: "
            f"{'PASS' if gates['latency_pass'] else 'FAIL'}"
            f"（{gates['direct_p50_ms']}ms vs {gates['brain_path_p50_ms']}ms）",
            f"- 总判定: {'**PASS**' if gates['pass'] else '**FAIL**'}",
            "",
            "## 逐题明细",
            "",
            "| 题目 | 期望 | 实际 | 级联 ms | 基线 ms | 级联 tok | 基线 tok | 复核(级/基) |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    for r in result.results:
        lines.append(
            f"| {r.question_id} | {r.expect_route} | {r.route or '失败'} "
            f"| {r.latency_ms} | {r.baseline_latency_ms} | {r.tokens} "
            f"| {r.baseline_tokens} "
            f"| {_fmt_flag(r.cascade_accepted)}/{_fmt_flag(r.baseline_accepted)} |"
        )
    lines.append("")
    return "\n".join(lines)


def _fmt_rate(value: float | None) -> str:
    return "—" if value is None else f"{value:.1%}"


def _fmt_delta(value: float | None) -> str:
    return "—" if value is None else f"{value:+.1f}"


def _fmt_ratio(value: float | None) -> str:
    return "—" if value is None else f"{value:.1%}"


def _fmt_flag(value: bool | None) -> str:
    return "—" if value is None else ("通过" if value else "未过")


def write_reports(result: BenchResult, out_dir: Path | str) -> dict[str, Path]:
    """JSON + Markdown 双格式落盘；返回路径。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "bench-report.json"
    md_path = out / "bench-report.md"
    payload = result.to_dict()
    payload["created"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md_path.write_text(render_markdown(result), encoding="utf-8")
    return {"json": json_path, "markdown": md_path}


__all__ = ["render_markdown", "write_reports"]
