"""T018：蒸馏数据集构造——轨迹抽取/gold 标注/去重/分层切分/导出（FR12）。

约束 C1：禁 mock。台账为真实 LedgerWriter 异步落盘（真线程真文件），
检索走真实 LedgerQuery 文件扫描。
"""

import json

import pytest

from jev.distill import GOLD_LABELS, build_dataset, export_dataset
from jev.errors import JevError
from jev.kernel.ledger.query import LedgerQuery
from jev.kernel.ledger.writer import LedgerEntry, LedgerWriter


def _decision(request_id: str, point_id: str, *, value="x", confidence=0.9,
              probabilities=None, from_fallback=False) -> LedgerEntry:
    return LedgerEntry(
        request_id=request_id, kind="decision", ts=1.0,
        point_id=point_id, stage="PRE", mode="active",
        question=f"{point_id}: 演示问题", question_type="choice",
        value=value, confidence=confidence,
        probabilities=probabilities or {"a": 0.9, "b": 0.1},
        applied=value, from_fallback=from_fallback,
    )


def _route(request_id: str, route: str, state: str) -> LedgerEntry:
    return LedgerEntry(
        request_id=request_id, kind="route", ts=1.0,
        route=route, state=state, posterior={"route": route},
    )


def _answer(request_id: str, content: str, post_verdict: str | None) -> LedgerEntry:
    return LedgerEntry(
        request_id=request_id, kind="answer", ts=2.0,
        content=content, post_verdict=post_verdict, tokens=64,
    )


@pytest.fixture()
def ledger(tmp_path):
    """真实异步台账（真写线程 + 真文件），用毕排空关闭。"""
    with LedgerWriter(dir=tmp_path / "ledger") as writer:
        yield writer
        writer.flush()


class TestBuildDataset:
    def test_extracts_full_accept_trajectories_with_outcome_gold(self, tmp_path, ledger):
        ledger.log(_route("req-ok", "full", '[{"role":"user","content":"写一篇综述"}]'))
        ledger.log(_decision("req-ok", "intent", value="complex", confidence=0.82))
        ledger.log(_decision("req-ok", "needs_retry", value="no_retry", confidence=0.91))
        ledger.log(_decision("req-ok", "verbosity"))  # 无 outcome gold 的点不入集
        ledger.log(_answer("req-ok", "大脑生成的综述正文", "accept"))
        # 非升级路径：小脑直答 → 排除
        ledger.log(_route("req-direct", "direct", '[{"role":"user","content":"你好"}]'))
        ledger.log(_decision("req-direct", "intent", value="decision", confidence=0.95))
        ledger.log(_answer("req-direct", "直答内容", None))
        # 升级但复核未通过（降级交付）→ 排除
        ledger.log(_route("req-degraded", "full", '[{"role":"user","content":"写代码"}]'))
        ledger.log(_decision("req-degraded", "intent", value="complex", confidence=0.6))
        ledger.log(_answer("req-degraded", "残缺答案", "downgrade"))
        ledger.flush()

        dataset = build_dataset(LedgerQuery(dir=tmp_path / "ledger"), min_samples=1)
        assert [s.point_id for s in dataset.samples] == ["intent", "needs_retry"]
        intent = dataset.samples[0]
        assert intent.gold == "complex"
        assert intent.gold_answer == "大脑生成的综述正文"
        assert intent.state == '[{"role":"user","content":"写一篇综述"}]'
        assert intent.value == "complex"
        assert intent.probabilities == {"a": 0.9, "b": 0.1}
        retry = dataset.samples[1]
        assert retry.gold == "no_retry"  # POST 复核通过 → "通过"侧标签

    def test_below_min_samples_raises_typed_error(self, tmp_path, ledger):
        ledger.log(_route("req-1", "full", '[{"role":"user","content":"q1"}]'))
        ledger.log(_decision("req-1", "intent"))
        ledger.log(_answer("req-1", "答案", "accept"))
        ledger.flush()

        with pytest.raises(JevError, match="样本不足"):
            build_dataset(LedgerQuery(dir=tmp_path / "ledger"), min_samples=500)

    def test_dedupes_same_state_and_point_across_requests(self, tmp_path, ledger):
        state = '[{"role":"user","content":"重复状态"}]'
        for req in ("req-a", "req-b"):
            ledger.log(_route(req, "full", state))
            ledger.log(_decision(req, "intent"))
            ledger.log(_answer(req, f"{req} 的答案", "accept"))
        ledger.flush()

        dataset = build_dataset(LedgerQuery(dir=tmp_path / "ledger"), min_samples=1)
        assert len(dataset.samples) == 1  # 保留首见
        assert dataset.samples[0].request_id == "req-a"


class TestStratifiedSplit:
    def test_split_is_stratified_deterministic_and_complete(self, tmp_path, ledger):
        for i in range(20):
            state = json.dumps([{"role": "user", "content": f"状态 {i}"}])
            req = f"req-{i}"
            ledger.log(_route(req, "full", state))
            ledger.log(_decision(req, "intent", value="complex"))
            ledger.log(_decision(req, "needs_retry", value="no_retry"))
            ledger.log(_answer(req, f"答案 {i}", "accept"))
        ledger.flush()

        dataset = build_dataset(LedgerQuery(dir=tmp_path / "ledger"), min_samples=1)
        # 每层 20 样本：train 14 / dev 3 / test 3（floor 语义）
        assert len(dataset.train) == 28
        assert len(dataset.dev) == 6
        assert len(dataset.test) == 6
        # 全集 = 三切分之并，无重叠
        all_ids = {s.request_id + s.point_id for s in dataset.samples}
        split_ids = (
            {s.request_id + s.point_id for s in dataset.train}
            | {s.request_id + s.point_id for s in dataset.dev}
            | {s.request_id + s.point_id for s in dataset.test}
        )
        assert all_ids == split_ids
        assert len(dataset.samples) == 40
        assert dataset.strata == {"intent": 20, "needs_retry": 20}


class TestExportDataset:
    def test_exports_jsonl_splits_and_manifest(self, tmp_path, ledger):
        for i in range(10):
            state = json.dumps([{"role": "user", "content": f"状态 {i}"}])
            req = f"req-{i}"
            ledger.log(_route(req, "full", state))
            ledger.log(_decision(req, "intent", value="complex"))
            ledger.log(_answer(req, f"答案 {i}", "accept"))
        ledger.flush()

        dataset = build_dataset(LedgerQuery(dir=tmp_path / "ledger"), min_samples=1)
        out = export_dataset(dataset, tmp_path / "distill" / "v1")

        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["kind"] == "jev-distill-dataset"
        assert manifest["counts"]["total"] == 10
        counts = manifest["counts"]
        assert counts["train"] + counts["dev"] + counts["test"] == counts["total"] == 10
        assert manifest["strata"] == {"intent": 10}
        assert manifest["gold_labels"] == GOLD_LABELS

        train_lines = (out / "train.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(train_lines) == manifest["counts"]["train"]
        sample = json.loads(train_lines[0])
        assert sample["point_id"] == "intent"
        assert sample["gold"] == "complex"
        assert sample["question_type"] == "choice"
        assert set(sample) == {
            "request_id", "point_id", "state", "question", "question_type",
            "gold", "gold_answer", "value", "confidence", "probabilities",
            "from_fallback",
        }
