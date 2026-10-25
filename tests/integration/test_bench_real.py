"""T021 IT：真实资产全量基准（BS-05 验收门）——真实 Laya 小脑 + 真实
llama-server 大脑（CUDA）+ 真实 18 决策点注册表 + configs/bench-set.yaml 20 题。

FR13：五类指标（延迟/命中率/精度保持/成本/决策点开销）+ BS-05 验收门
（命中率 ≥70% 且 直答 P50 ≤ 大脑路径 P50×1/3）。门 FAIL 即真实 ST 发现。
"""

from pathlib import Path

import pytest

from jev.bench.report import write_reports
from jev.bench.runner import load_bench_set, run_bench

pytestmark = [pytest.mark.assets, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestBenchRealAssets:
    def test_full_bench_set_meets_bs05_gates(self, real_kernel, brain_client, tmp_path):
        questions = load_bench_set(REPO_ROOT / "configs" / "bench-set.yaml")
        assert len(questions) == 40

        result = run_bench(
            cerebellum=real_kernel,
            registry=real_kernel.registry,
            brain=brain_client,
            questions=questions,
            include_baseline=True,
        )

        # 级联与基线双侧全部成功（真实资产下无失败题）
        assert all(r.ok for r in result.results), [
            (r.question_id, r.route) for r in result.results if not r.ok
        ]
        assert all(r.baseline_ok for r in result.results)

        # 路由分布覆盖基准集预期形态：direct 为主、三路径齐全
        counts = result.routes["counts"]
        assert counts["direct"] + counts["light"] + counts["full"] == 40
        assert counts["direct"] > 0

        # 精度保持：双侧复核均有值（POST 决策点真实前向）
        assert result.precision["cascade_acceptance"] is not None
        assert result.precision["baseline_acceptance"] is not None

        # 决策点开销：真实批量前向（小脑 CPU EP 预算 P50 < 20ms，留裕量断言 < 100ms）
        assert result.decision_overhead["n"] == 5
        assert result.decision_overhead["batch_questions"] > 0
        assert result.decision_overhead["p50"] < 100.0

        # 报告落盘可读
        paths = write_reports(result, tmp_path)
        assert paths["json"].is_file() and paths["markdown"].is_file()

        # BS-05 验收门：FAIL 即真实 ST 发现（附完整指标供诊断）
        gates = result.gates
        detail = (
            f"\n路由分布: {counts}"
            f"\n延迟: direct P50={gates['direct_p50_ms']}ms "
            f"大脑路径 P50={gates['brain_path_p50_ms']}ms "
            f"级联全程 P50={result.latency['cascade']['p50']}ms "
            f"基线 P50={result.latency['baseline_brain']['p50']}ms"
            f"\n精度: 级联通过率={result.precision['cascade_acceptance']} "
            f"基线={result.precision['baseline_acceptance']} "
            f"delta={result.precision['delta_pt']}pt"
            f"\n逐题路由: {[(r.question_id, r.route) for r in result.results]}"
        )
        assert gates["hit_rate_pass"], f"BS-05 命中率门 FAIL: {gates['hit_rate']:.1%}{detail}"
        assert gates["latency_pass"], (
            f"BS-05 延迟门 FAIL: direct P50 {gates['direct_p50_ms']}ms "
            f"> 大脑路径 P50 {gates['brain_path_p50_ms']}ms / 3{detail}"
        )
        assert gates["pass"], f"BS-05 总判定 FAIL{detail}"

        # BS-01 延迟量级（返工修订口径）：路径① < 700ms；路径② < 路径③
        by_route = result.latency["by_route"]
        assert gates["direct_p50_ms"] < 700.0, (
            f"BS-01 路径① P50 {gates['direct_p50_ms']}ms ≥ 700ms{detail}"
        )
        if "light" in by_route and "full" in by_route:
            assert by_route["light"]["p50"] < by_route["full"]["p50"], (
                f"BS-01 路径② P50 {by_route['light']['p50']}ms "
                f"未低于路径③ {by_route['full']['p50']}ms{detail}"
            )


class TestLedgerAggregationRealAssets:
    """BS-02：18 决策点全链路台账——request_id 聚合还原完整决策链。"""

    def test_light_request_logs_full_decision_chain(
        self, real_kernel, brain_client, tmp_path
    ):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from jev.bench.runner import load_bench_set
        from jev.kernel.cascade.engine import CascadeEngine
        from jev.kernel.ledger.query import LedgerQuery
        from jev.kernel.ledger.writer import LedgerWriter
        from jev.server.chat import create_chat_router

        ledger = LedgerWriter(dir=tmp_path / "ledger")
        registry = real_kernel.registry
        app = FastAPI()
        app.include_router(
            create_chat_router(
                real_kernel, brain_client, registry,
                engine=CascadeEngine(registry), ledger=ledger,
            )
        )
        client = TestClient(app)

        # 简单生成题（期望路径②）：max_tokens=128 → 单段 → 全 18 点各记录一次
        light_questions = [
            q for q in load_bench_set(REPO_ROOT / "configs" / "bench-set.yaml")
            if q.expect_route == "light"
        ]
        assert len(light_questions) == 8
        routed: list[tuple[str, str]] = []
        for question in light_questions:
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "messages": [dict(m) for m in question.messages],
                    "max_tokens": 128,
                },
            )
            assert resp.status_code == 200
            request_id = resp.json()["id"].removeprefix("chatcmpl-jev-")
            routed.append((request_id, resp.headers["x-jev-route"]))

        ledger.flush()
        ledger.close()
        query = LedgerQuery(dir=tmp_path / "ledger")

        # BS-01 抽样口径：简单生成题至少一题命中路径②（未命中即真实 ST 发现）
        light_ids = [rid for rid, route in routed if route == "light"]
        assert light_ids, f"4 道简单生成题均未路由 light: {routed}"

        import collections

        for request_id, route in routed:
            entries = query.query(request_id)
            kinds = collections.Counter(e.kind for e in entries)
            assert kinds["route"] == 1 and kinds["answer"] == 1
            decisions = [e for e in entries if e.kind == "decision"]
            stages = collections.Counter(e.stage for e in decisions)
            if route == "light":
                # PRE9 + MID4×段数 + POST5（单段=18，多段 MID 成倍）
                assert stages["PRE"] == 9
                assert stages["MID"] >= 4 and stages["MID"] % 4 == 0
                assert stages["POST"] == 5
            elif route == "full":
                # full 不评估 MID：PRE9 + POST5 = 14
                assert stages["PRE"] == 9
                assert stages["MID"] == 0
                assert stages["POST"] == 5
            elif route == "direct":
                # direct 在 PRE 后即直答，无 MID/POST
                assert stages["PRE"] == 9
                assert stages["MID"] == 0
                assert stages["POST"] == 0
