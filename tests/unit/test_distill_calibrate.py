"""T019：蒸馏校准——温度缩放/阈值扫描/NLL-ECE 留出集评估/劣化拒绝（FR12）。

约束 C1：禁 mock（纯逻辑校准数学 + 真实 builtin 注册表 + 真文件落盘）。
样本为 TrajectorySample 直构（T018 已用真实台账验证抽取链路）。
"""

import json

import pytest
from typer.testing import CliRunner

from jev.cli.main import app as cli_app
from jev.distill import (
    DistillDataset,
    TrajectorySample,
    calibrate,
    save_calibration,
)
from jev.errors import JevError
from jev.kernel.decisions.builtin import load_builtin_registry
from jev.kernel.ledger.writer import LedgerEntry, LedgerWriter

runner = CliRunner()
REGISTRY = load_builtin_registry()


def _sample(
    point_id: str,
    gold: str,
    probabilities: dict[str, float],
    *,
    question_type: str = "choice",
    state: str = "s",
) -> TrajectorySample:
    return TrajectorySample(
        request_id=f"req-{state}",
        point_id=point_id,
        state=state,
        question=f"{point_id}: 演示问题",
        question_type=question_type,
        gold=gold,
        gold_answer="大脑答案",
        value=max(probabilities, key=probabilities.__getitem__),
        confidence=0.5,
        probabilities=probabilities,
        from_fallback=False,
    )


def _dataset(samples: list[TrajectorySample], *, n_test: int = 3, n_dev: int = 1):
    """确定性切分：尾部 n_dev+n_dev 为 dev/test（纯逻辑直构，分层已由 T018 覆盖）。"""
    test = samples[-n_test:]
    dev = samples[-n_test - n_dev : -n_test]
    train = samples[: -n_test - n_dev]
    return DistillDataset(
        samples=tuple(samples),
        train=tuple(train),
        dev=tuple(dev),
        test=tuple(test),
    )


class TestCalibrate:
    def test_temperature_scaling_improves_nll_and_ece(self):
        # 全体样本：argmax 正确但欠置信（conf 0.55 < 基线 tau 0.7 走 fallback）
        samples = [
            _sample(
                "intent", "complex",
                {"complex": 0.70, "simple": 0.20, "decision": 0.10},
                state=f"s{i}",
            )
            for i in range(8)
        ]
        report = calibrate(_dataset(samples), REGISTRY)

        # 拟合温度锐化（T<1）：p_gold 0.70→接近 1
        assert report.temperature["choice"] < 1.0
        # 阈值扫描：全程正确（argmax 与 fallback 均 gold）→ 最小 tau
        assert report.tau["intent"] == 0.0
        # 留出集：NLL/ECE 均优于基线（T=1 + 原 tau 0.7）
        assert report.test.nll < report.baseline_test.nll
        assert report.test.ece < report.baseline_test.ece
        assert report.test.accuracy == 1.0
        assert report.accepted is True

    def test_tau_scan_gates_out_confident_wrong_predictions(self):
        # 高置信正确（conf≈0.925）+ 次高置信错误（conf≈0.775 > 基线 tau 0.7）
        good = {"complex": 0.95, "simple": 0.025, "decision": 0.025}
        bad = {"simple": 0.85, "complex": 0.075, "decision": 0.075}
        samples = []
        for i in range(8):
            samples.append(_sample("intent", "complex", good, state=f"g{i}"))
            samples.append(_sample("intent", "complex", bad, state=f"b{i}"))
        report = calibrate(_dataset(samples, n_test=4), REGISTRY)

        # 门控语义（温度无关）：错误预测被门掉（coverage<1）、正确预测保留（coverage>0）
        assert report.test.accuracy == 1.0
        assert 0.0 < report.test.coverage < 1.0
        assert report.baseline_test.accuracy < 1.0  # 基线 tau 0.7 放进错误预测

    def test_degraded_holdout_rejects_publication(self):
        # train 过自信全错 → 拟合大 T（抹平）；test 已校准且正确 → 大 T 反而劣化
        wrong = {"simple": 0.90, "complex": 0.05, "decision": 0.05}
        right = {"complex": 0.90, "simple": 0.05, "decision": 0.05}
        train = [_sample("intent", "complex", wrong, state=f"t{i}") for i in range(8)]
        test = [_sample("intent", "complex", right, state=f"e{i}") for i in range(3)]
        dataset = DistillDataset(
            samples=tuple(train + test),
            train=tuple(train),
            dev=(),
            test=tuple(test),
        )
        report = calibrate(dataset, REGISTRY)

        assert report.temperature["choice"] > 1.0  # train 驱动抹平
        assert report.test.nll > report.baseline_test.nll  # 留出集劣化
        assert report.accepted is False  # 拒绝发布

    def test_empty_holdout_raises_typed_error(self):
        samples = [
            _sample("intent", "complex", {"complex": 0.9, "simple": 0.1}, state=f"s{i}")
            for i in range(4)
        ]
        dataset = DistillDataset(
            samples=tuple(samples), train=tuple(samples), dev=(), test=()
        )
        with pytest.raises(JevError, match="留出集为空"):
            calibrate(dataset, REGISTRY)

    def test_save_calibration_round_trip(self, tmp_path):
        samples = [
            _sample(
                "intent", "complex",
                {"complex": 0.70, "simple": 0.20, "decision": 0.10},
                state=f"s{i}",
            )
            for i in range(8)
        ]
        report = calibrate(_dataset(samples), REGISTRY)
        path = save_calibration(report, tmp_path / "calibration.json")

        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["kind"] == "jev-distill-calibration"
        assert payload["temperature"] == report.temperature
        assert payload["tau"] == report.tau
        assert payload["accepted"] is report.accepted
        assert set(payload["metrics"]) == {"train", "test", "baseline_test"}


class TestDistillCli:
    def test_distill_calibrate_end_to_end(self, tmp_path):
        ledger_dir = tmp_path / "ledger"
        with LedgerWriter(dir=ledger_dir) as writer:
            for i in range(12):
                req = f"req-{i}"
                writer.log(
                    LedgerEntry(
                        request_id=req, kind="route", ts=1.0, route="full",
                        state=f'[{{"role":"user","content":"状态 {i}"}}]',
                        posterior={"route": "full"},
                    )
                )
                writer.log(
                    LedgerEntry(
                        request_id=req, kind="decision", ts=1.0,
                        point_id="intent", stage="PRE", mode="active",
                        question="intent: 决策还是闲聊？", question_type="choice",
                        value="complex", confidence=0.82,
                        probabilities={"decision": 0.08, "simple": 0.10, "complex": 0.82},
                        applied="complex", from_fallback=False, latency_ms=12.0,
                    )
                )
                writer.log(
                    LedgerEntry(
                        request_id=req, kind="decision", ts=1.0,
                        point_id="needs_retry", stage="POST", mode="active",
                        question="needs_retry: 需要重试吗？", question_type="choice",
                        value="no_retry", confidence=0.8,
                        probabilities={"retry": 0.2, "no_retry": 0.8},
                        applied="no_retry", from_fallback=False, latency_ms=11.0,
                    )
                )
                writer.log(
                    LedgerEntry(
                        request_id=req, kind="answer", ts=2.0,
                        content=f"大脑答案 {i}", post_verdict="accept", tokens=64,
                    )
                )
            writer.flush()

        out = tmp_path / "distill-out"
        result = runner.invoke(
            cli_app,
            [
                "distill", "--calibrate",
                "--min-samples", "10",
                "--ledger-dir", str(ledger_dir),
                "--decision-points", "configs/decision-points.toml",
                "--out", str(out),
            ],
        )
        assert result.exit_code == 0, result.output
        # 数据集 + 校准文件 + 报告齐备（版本化产物）
        assert (out / "train.jsonl").is_file()
        assert (out / "test.jsonl").is_file()
        assert (out / "manifest.json").is_file()
        calibration = json.loads((out / "calibration.json").read_text(encoding="utf-8"))
        counts = calibration["counts"]
        assert counts["train"] + counts["dev"] + counts["test"] == 24
        assert counts["points"] == 2
        assert set(calibration["tau"]) == {"intent", "needs_retry"}
        assert "数据集就位" in result.output

    def test_distill_below_min_samples_exits_nonzero(self, tmp_path):
        ledger_dir = tmp_path / "ledger"
        with LedgerWriter(dir=ledger_dir) as writer:
            writer.log(
                LedgerEntry(
                    request_id="req-1", kind="route", ts=1.0, route="full",
                    state='[{"role":"user","content":"唯一状态"}]',
                )
            )
            writer.log(
                LedgerEntry(
                    request_id="req-1", kind="decision", ts=1.0,
                    point_id="intent", stage="PRE", mode="active",
                    question="intent: ?", question_type="choice",
                    value="complex", confidence=0.8,
                    probabilities={"complex": 0.8, "simple": 0.2},
                )
            )
            writer.log(
                LedgerEntry(
                    request_id="req-1", kind="answer", ts=2.0,
                    content="答案", post_verdict="accept",
                )
            )
            writer.flush()

        result = runner.invoke(
            cli_app,
            [
                "distill", "--ledger-dir", str(ledger_dir),
                "--decision-points", "configs/decision-points.toml",
                "--min-samples", "500",
                "--out", str(tmp_path / "out"),
            ],
        )
        assert result.exit_code != 0
