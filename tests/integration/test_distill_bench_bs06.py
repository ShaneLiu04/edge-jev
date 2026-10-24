"""BS-06（FR12-2）：蒸馏闭环——升级轨迹 → distill 校准 → bench 复测精度不回退。

真实资产全链路：真 laya 小脑 + 真 llama-server 大脑 + 真 18 决策点注册表。
门（design §6.3 BS-06）：校准发布后 bench 复测 精度差 ≥ -2pt 且命中率不下降；
校准被拒绝发布（留出集劣化）时保守侧保持旧参数——门自动满足。
样本口径：ST 环境以 bench-set 复杂题构造轨迹（min_samples 下调；
生产口径 ≥500 轨迹见 design 原文，量级差异记录于 tasks.md）。
"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jev.bench.runner import load_bench_set, run_bench
from jev.distill import build_dataset, calibrate, save_calibration
from jev.errors import JevError
from jev.kernel.cascade.engine import CascadeEngine
from jev.kernel.cerebellum.laya_onnx import LayaOnnxBackend
from jev.kernel.ledger.query import LedgerQuery
from jev.kernel.ledger.writer import LedgerWriter
from jev.server.chat import create_chat_router

pytestmark = [pytest.mark.assets, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]
MODELS = REPO_ROOT / "models" / "laya"


@pytest.fixture()
def ledger(tmp_path) -> LedgerWriter:
    return LedgerWriter(dir=tmp_path / "ledger")


def _chat_app(real_kernel, brain_client, ledger):
    registry = real_kernel.registry
    app = FastAPI()
    app.include_router(
        create_chat_router(
            real_kernel, brain_client, registry,
            engine=CascadeEngine(registry), ledger=ledger,
        )
    )
    return TestClient(app)


def _questions_by_expect(expect: str) -> list:
    return [
        q for q in load_bench_set(REPO_ROOT / "configs" / "bench-set.yaml")
        if q.expect_route == expect
    ]


class TestDistillBenchLoop:
    def test_calibration_publish_keeps_precision_and_hit_rate(
        self, real_kernel, brain_client, tmp_path, ledger
    ):
        # 1) 构造升级轨迹：复杂题（期望 full）经真实级联 + POST 复核入台账
        client = _chat_app(real_kernel, brain_client, ledger)
        full_questions = _questions_by_expect("full")
        assert len(full_questions) == 6
        for question in full_questions:
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "messages": [dict(m) for m in question.messages],
                    "max_tokens": 96,
                },
            )
            assert resp.status_code == 200
        ledger.flush()
        ledger.close()

        # 2) distill：台账 → 数据集 → 校准（拒绝发布则门自动满足）
        query = LedgerQuery(dir=tmp_path / "ledger")
        dataset = build_dataset(query, min_samples=8)
        assert dataset.samples, "升级轨迹为空：full+accept 路径未产生样本（真实 ST 发现）"
        report = calibrate(dataset, real_kernel.registry)
        calibration_path = save_calibration(report, tmp_path / "calibration.json")
        assert calibration_path.is_file()

        # 3) 复测子集（混合分层，控制时长）：真基线对比
        subset = (
            _questions_by_expect("direct")[:3]
            + _questions_by_expect("light")[:1]
            + _questions_by_expect("full")[:2]
        )
        assert len(subset) == 6
        pre = run_bench(
            cerebellum=real_kernel,
            registry=real_kernel.registry,
            brain=brain_client,
            questions=subset,
            include_baseline=True,
        )

        if not report.accepted:
            # 保守侧：拒绝发布 → 服务参数不变 → 精度/命中率不变（门平凡满足）
            post = run_bench(
                cerebellum=real_kernel,
                registry=real_kernel.registry,
                brain=brain_client,
                questions=subset,
                include_baseline=True,
            )
        else:
            calibrated = LayaOnnxBackend(
                model_path=MODELS / "laya.onnx",
                tokenizer_path=MODELS / "tokenizer" / "tokenizer.json",
                config_path=MODELS / "laya_config.json",
                calibration=report.temperature,
            )
            post = run_bench(
                cerebellum=calibrated,
                registry=real_kernel.registry,
                brain=brain_client,
                questions=subset,
                include_baseline=True,
            )

        # BS-06 门：精度差 ≥ -2pt 且命中率不下降
        detail = (
            f"\naccepted={report.accepted}"
            f"\npre:  delta_pt={pre.precision['delta_pt']} "
            f"hit_rate={pre.routes['hit_rate']:.1%}"
            f"\npost: delta_pt={post.precision['delta_pt']} "
            f"hit_rate={post.routes['hit_rate']:.1%}"
        )
        pre_delta = pre.precision["delta_pt"] or 0.0
        post_delta = post.precision["delta_pt"] or 0.0
        assert post_delta >= pre_delta - 2.0, (
            f"BS-06 精度门 FAIL: 复测 {post_delta}pt < 校准前 {pre_delta}pt - 2{detail}"
        )
        assert post.routes["hit_rate"] >= pre.routes["hit_rate"] - 1e-9, (
            f"BS-06 命中率门 FAIL: 复测 {post.routes['hit_rate']:.1%} "
            f"< 校准前 {pre.routes['hit_rate']:.1%}{detail}"
        )

    def test_distill_raises_without_trajectories(self, real_kernel, tmp_path):
        """空台账（无升级轨迹）→ JevError（缺样本显式报错而非静默空集）。"""
        empty_dir = tmp_path / "empty-ledger"
        empty_dir.mkdir()
        with pytest.raises(JevError):
            build_dataset(LedgerQuery(dir=empty_dir), min_samples=8)
