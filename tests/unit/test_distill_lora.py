"""T020：kev LoRA 微调路径——数据桥/命令构造/子进程编排（FR12 可选路径）。

约束 C1：禁 mock。记录转换为纯逻辑（真实 builtin 注册表）；JSONL 为真
文件 round-trip；子进程编排用真实 python 子进程（成功/非零退出/不可执
行三路径）；真实 GPU 训练执行留 ST 阶段（kev 需独立 py3.12 环境）。
"""

import json
import sys

import pytest

from jev.distill.dataset import DistillDataset, TrajectorySample
from jev.distill.lora import (
    build_train_command,
    run_training,
    to_kev_records,
    train_lora,
    write_training_jsonl,
)
from jev.errors import JevAssetError, JevError
from jev.kernel.decisions.builtin import load_builtin_registry

REGISTRY = load_builtin_registry()


def _sample(point_id: str, gold: str, probabilities: dict, *, state: str,
            question_type: str = "choice") -> TrajectorySample:
    return TrajectorySample(
        request_id="req-1",
        point_id=point_id,
        state=state,
        question=f"{point_id}: 演示问题",
        question_type=question_type,
        gold=gold,
        gold_answer="大脑答案",
        value=max(probabilities, key=probabilities.__getitem__),
        confidence=0.9,
        probabilities=probabilities,
        from_fallback=False,
    )


def _dataset(samples: list[TrajectorySample]) -> DistillDataset:
    return DistillDataset(
        samples=tuple(samples), train=tuple(samples), dev=(), test=()
    )


MESSAGES_STATE = json.dumps(
        [{"role": "user", "content": "写一篇综述"}, {"role": "assistant", "content": "好的"}],
        ensure_ascii=False,
)


class TestToKevRecords:
    def test_converts_grouped_by_request_with_labels(self):
        samples = [
            _sample(
                "intent", "complex",
                {"decision": 0.1, "simple": 0.1, "complex": 0.8},
                state=MESSAGES_STATE,
            ),
            _sample(
                "needs_retry", "no_retry",
                {"retry": 0.2, "no_retry": 0.8},
                state=MESSAGES_STATE,
            ),
        ]
        records = to_kev_records(_dataset(samples), REGISTRY)

        assert len(records) == 1  # 同 request 聚合为一条记录
        record = records[0]
        # state 展平：messages JSON → 纯文本（逐消息 content 换行连接）
        assert record["state"] == "写一篇综述\n好的"
        intent = record["questions"]["intent"]
        assert intent["type"] == "choice"
        assert intent["instructions"] == "intent: 演示问题"
        assert intent["criteria"] == {"decision": None, "simple": None, "complex": None}
        assert intent["label"] == "complex"  # choice label = 选项名
        retry = record["questions"]["needs_retry"]
        assert retry["label"] == "no_retry"

    def test_score_point_uses_level_index_label(self):
        samples = [
            _sample(
                "complexity", "high",
                {"0": 0.1, "1": 0.2, "2": 0.7},
                state=MESSAGES_STATE, question_type="score",
            ),
        ]
        records = to_kev_records(_dataset(samples), REGISTRY)

        question = records[0]["questions"]["complexity"]
        assert question["type"] == "score"
        assert question["criteria"] == ["low", "medium", "high"]  # 等级列表
        assert question["label"] == 2  # score label = 等级索引


class TestWriteTrainingJsonl:
    def test_jsonl_round_trip_matches_kev_load_records_shape(self, tmp_path):
        samples = [
            _sample(
                "intent", "complex",
                {"decision": 0.1, "simple": 0.1, "complex": 0.8},
                state=MESSAGES_STATE,
            ),
        ]
        records = to_kev_records(_dataset(samples), REGISTRY)
        path = write_training_jsonl(records, tmp_path / "lora-data.jsonl")

        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        # kev load_records 契约：state + 非空 questions + 每问 label
        assert isinstance(record["state"], str)
        assert record["questions"]
        for question in record["questions"].values():
            assert "label" in question
            assert "type" in question
            assert "instructions" in question


class TestTrainCommand:
    def test_builds_uv_isolated_command(self, tmp_path):
        repo = tmp_path / "kev-repo"
        (repo / "kev").mkdir(parents=True)
        (repo / "kev" / "train.py").write_text("# kev train", encoding="utf-8")
        command = build_train_command(
            repo, tmp_path / "data.jsonl", tmp_path / "adapter",
            device="cuda", epochs=2, lora_rank=8,
        )
        assert command[:3] == ["uv", "run", "--project"]
        joined = " ".join(command)
        assert "python -m kev.train" in joined
        assert "--data" in command and "--out" in command
        assert "--device cuda" in joined and "--epochs 2" in joined and "--lora 8" in joined

    def test_missing_kev_repo_raises_asset_error(self, tmp_path):
        with pytest.raises(JevAssetError, match="kev 仓库不完整"):
            build_train_command(tmp_path / "nope", "d.jsonl", "out")


class TestRunTraining:
    def test_success_path_runs_real_subprocess(self, tmp_path):
        script = tmp_path / "ok.py"
        script.write_text("print('training done')", encoding="utf-8")
        run_training([sys.executable, str(script)], cwd=tmp_path)  # 不抛即通过

    def test_nonzero_exit_raises_with_diagnostics(self, tmp_path):
        script = tmp_path / "fail.py"
        script.write_text(
            "import sys; print('boom stdout'); sys.stderr.write('boom stderr'); sys.exit(3)",
            encoding="utf-8",
        )
        with pytest.raises(JevError, match="exit 3"):
            run_training([sys.executable, str(script)], cwd=tmp_path)

    def test_unreachable_command_raises_typed_error(self, tmp_path):
        with pytest.raises(JevError, match="不可执行"):
            run_training(["definitely-not-a-real-binary", "--x"], cwd=tmp_path)


class TestTrainLora:
    def test_composition_writes_data_and_reports_missing_repo(self, tmp_path):
        samples = [
            _sample(
                "intent", "complex",
                {"decision": 0.1, "simple": 0.1, "complex": 0.8},
                state=MESSAGES_STATE,
            ),
        ]
        with pytest.raises(JevAssetError, match="kev 仓库不完整"):
            train_lora(
                _dataset(samples), REGISTRY,
                tmp_path / "no-kev", tmp_path / "lora-out",
            )
        # 组合入口先落训练数据（诊断可见输入规模）
        assert (tmp_path / "lora-out" / "lora-data.jsonl").is_file()

    def test_empty_convertible_samples_raise(self, tmp_path):
        # noul 型/未注册点 → 无可转换样本
        sample = TrajectorySample(
            request_id="r", point_id="unknown_point", state=MESSAGES_STATE,
            question="q", question_type="noul", gold="true", gold_answer="a",
            value="true", confidence=0.9, probabilities={"true": 0.9, "false": 0.1},
            from_fallback=False,
        )
        with pytest.raises(JevError, match="无可转换样本"):
            train_lora(
                _dataset([sample]), REGISTRY,
                tmp_path, tmp_path / "lora-out",
            )
