"""T021：基准指标计算（UT-09，FR13）。纯逻辑无后端。"""

import math

from jev.bench.metrics import (
    bs05_gates,
    cost_summary,
    latency_summary,
    percentile,
    precision_delta_pt,
    route_summary,
)


class TestPercentile:
    def test_linear_interpolation(self):
        assert percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.5
        assert percentile([1.0, 2.0, 3.0, 4.0], 0) == 1.0
        assert percentile([1.0, 2.0, 3.0, 4.0], 100) == 4.0
        assert percentile([1.0, 2.0, 3.0, 4.0], 25) == 1.75

    def test_edge_cases(self):
        assert percentile([7.0], 95) == 7.0
        assert math.isnan(percentile([], 50))


class TestLatencySummary:
    def test_summary_fields(self):
        values = [10.0, 20.0, 30.0, 40.0, 100.0]
        summary = latency_summary(values)
        assert summary["n"] == 5
        assert summary["min"] == 10.0
        assert summary["max"] == 100.0
        assert summary["p50"] == 30.0
        assert summary["p95"] == 88.0  # 4 与 100 之间插值

    def test_empty_summary(self):
        assert latency_summary([])["p50"] is None


class TestRouteSummary:
    def test_hit_rate_counts_direct_and_light(self):
        summary = route_summary(["direct", "light", "light", "full", None])
        assert summary["counts"] == {
            "direct": 1, "light": 2, "full": 1, "failed": 1,
        }
        assert summary["hit_rate"] == 0.6
        assert summary["direct_rate"] == 0.2

    def test_total_zero(self):
        assert route_summary([])["hit_rate"] == 0.0


class TestPrecisionDelta:
    def test_pairwise_delta_in_points(self):
        cascade = [True, True, None, False]   # direct 题 None 不计入
        baseline = [True, False, True, True]
        delta = precision_delta_pt(cascade, baseline)
        # 成对可复核 3 题：级联 2/3，基线 2/3 → 0pt
        assert delta == 0.0

    def test_all_unreviewable_is_none(self):
        assert precision_delta_pt([None, None], [True, False]) is None

    def test_negative_when_cascade_worse(self):
        delta = precision_delta_pt([False, False], [True, True])
        assert delta == -100.0


class TestCostSummary:
    def test_totals_per_route_and_savings(self):
        cost = cost_summary(
            [0, 50, 200], [300, 300, 300], ["direct", "light", "full"]
        )
        assert cost["cascade_total_tokens"] == 250
        assert cost["baseline_total_tokens"] == 900
        assert cost["avg_tokens_by_route"] == {"direct": 0.0, "full": 200.0, "light": 50.0}
        assert cost["savings_ratio"] == round(1 - 250 / 900, 4)

    def test_zero_baseline_savings_none(self):
        cost = cost_summary([10], [0], ["direct"])
        assert cost["savings_ratio"] is None


class TestBs05Gates:
    def test_pass_when_hit_rate_and_latency_met(self):
        routes = ["direct"] * 8 + ["full"] * 2
        latencies = [100.0] * 8 + [900.0] * 2
        gates = bs05_gates(routes, latencies, [900.0] * 10)
        assert gates["hit_rate"] == 0.8
        assert gates["hit_rate_pass"] is True
        assert gates["direct_p50_ms"] == 100.0
        assert gates["brain_path_p50_ms"] == 900.0  # full 路径优先于基线
        assert gates["latency_pass"] is True  # 100 ≤ 900/3
        assert gates["pass"] is True

    def test_fail_on_low_hit_rate(self):
        routes = ["direct"] * 3 + ["full"] * 7
        gates = bs05_gates(routes, [100.0] * 10, [900.0] * 10)
        assert gates["hit_rate_pass"] is False
        assert gates["pass"] is False

    def test_fail_on_slow_direct(self):
        routes = ["direct"] * 8 + ["full"] * 2
        gates = bs05_gates(routes, [400.0] * 10, [900.0] * 10)
        assert gates["latency_pass"] is False  # 400 > 900/3

    def test_no_full_falls_back_to_baseline(self):
        gates = bs05_gates(["direct"] * 10, [50.0] * 10, [600.0] * 10)
        assert gates["brain_path_p50_ms"] == 600.0
        assert gates["latency_pass"] is True
