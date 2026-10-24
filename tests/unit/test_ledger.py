"""T012：决策台账 ledger 测试——异步落盘/按 request_id 聚合/JSONL 导出/<1ms 主路径。

约束 C1：真实文件系统 + 真实写线程 + 真实队列（标准库），无任何 mock。
磁盘失败场景用真实 OSError（把台账文件替换为同名目录，令 open/write 失败）。
"""

import json
import logging
import time
from pathlib import Path

import pytest

from jev.kernel.ledger.query import LedgerQuery
from jev.kernel.ledger.writer import LedgerEntry, LedgerWriter


def decision_entry(request_id: str = "req-1", **overrides) -> LedgerEntry:
    """一条典型决策 entry（PRE intent 点，全字段）。"""
    fields = dict(
        request_id=request_id,
        kind="decision",
        ts=time.time(),
        point_id="intent",
        stage="PRE",
        mode="active",
        question="Is this a decision task?",
        question_type="choice",
        value="decision",
        confidence=0.92,
        probabilities={"decision": 0.92, "generation": 0.08},
        applied="decision",
        from_fallback=False,
        breaker_state="closed",
        latency_ms=95.5,
    )
    fields.update(overrides)
    return LedgerEntry(**fields)


@pytest.fixture()
def writer(tmp_path: Path) -> LedgerWriter:
    w = LedgerWriter(dir=tmp_path)
    yield w
    w.close()


@pytest.fixture()
def query(tmp_path: Path) -> LedgerQuery:
    return LedgerQuery(dir=tmp_path)


class TestRoundtrip:
    def test_query_by_request_id(self, writer, query, tmp_path):
        writer.log(decision_entry("req-1"))
        writer.log(decision_entry("req-1", point_id="safety"))
        writer.log(decision_entry("req-2"))
        writer.close()

        entries = query.query("req-1")
        assert [e.point_id for e in entries] == ["intent", "safety"]
        assert all(e.request_id == "req-1" for e in entries)
        assert query.query("req-none") == []

    def test_decision_fields_persisted(self, writer, query):
        entry = decision_entry(mode="shadow")
        writer.log(entry)
        writer.close()

        loaded = query.query("req-1")[0]
        assert loaded.point_id == "intent"
        assert loaded.mode == "shadow"  # FR8：shadow 态标记可查
        assert loaded.probabilities == {"decision": 0.92, "generation": 0.08}
        assert loaded.applied == "decision"
        assert loaded.latency_ms == pytest.approx(95.5)
        assert loaded.breaker_state == "closed"
        assert loaded.ts > 0

    def test_route_and_answer_entries_roundtrip(self, writer, query):
        writer.log(
            LedgerEntry(
                request_id="req-9",
                kind="route",
                ts=time.time(),
                route="light",
                state="用户想翻译一句话",
                posterior={"intent": "generation"},
                breaker_state="closed",
            )
        )
        writer.log(
            LedgerEntry(
                request_id="req-9",
                kind="answer",
                ts=time.time(),
                route="light",
                content="Here is the translation.",
                post_verdict="accept",
                tokens=42,
            )
        )
        writer.close()

        entries = query.query("req-9")
        assert entries[0].kind == "route"
        assert entries[0].state == "用户想翻译一句话"
        assert entries[0].posterior == {"intent": "generation"}
        assert entries[1].kind == "answer"
        assert entries[1].content == "Here is the translation."
        assert entries[1].post_verdict == "accept"
        assert entries[1].tokens == 42

    def test_jsonl_encoding_utf8_no_ascii_escape(self, writer, tmp_path):
        writer.log(decision_entry(question="这是一个中文问题？"))
        writer.close()
        raw = (tmp_path / "jev-ledger.jsonl").read_text(encoding="utf-8")
        assert "这是一个中文问题？" in raw  # 中文原样落盘（非 \uXXXX）
        assert raw.endswith("\n")
        json.loads(raw.strip())  # 每行合法 JSON


class TestAsyncSemantics:
    def test_flush_makes_pending_visible(self, writer, query):
        writer.log(decision_entry("req-f"))
        writer.flush(timeout_s=5.0)
        assert len(query.query("req-f")) == 1
        writer.log(decision_entry("req-f", point_id="safety"))
        writer.flush(timeout_s=5.0)
        assert len(query.query("req-f")) == 2

    def test_close_drains_all_pending(self, writer, tmp_path):
        for i in range(200):
            writer.log(decision_entry("req-bulk", point_id=f"p{i}"))
        writer.close()
        lines = (tmp_path / "jev-ledger.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 200
        assert writer.written == 200
        assert writer.dropped == 0

    def test_main_path_overhead_under_1ms(self, writer):
        """FR8：主路径写入开销 <1ms（put_nowait 口径，100 次均值）。"""
        entries = [decision_entry("req-perf", point_id=f"p{i}") for i in range(100)]
        start = time.perf_counter()
        for entry in entries:
            writer.log(entry)
        elapsed_ms = (time.perf_counter() - start) / len(entries) * 1000
        assert elapsed_ms < 1.0, f"主路径 log 均值 {elapsed_ms:.3f}ms（FR8 要求 <1ms）"


class TestDiskFailure:
    def test_write_failure_drops_and_warns_not_blocks(self, writer, query, tmp_path, caplog):
        writer.log(decision_entry("req-ok"))
        writer.flush(timeout_s=5.0)
        assert writer.written == 1

        # 真实磁盘错误：台账文件替换为同名目录 → open/append 失败
        ledger_file = tmp_path / "jev-ledger.jsonl"
        ledger_file.unlink()
        ledger_file.mkdir()

        with caplog.at_level(logging.ERROR, logger="jev.kernel.ledger.writer"):
            for i in range(100):  # 恰达 warn_after_failures=100
                writer.log(decision_entry("req-fail", point_id=f"p{i}"))
                start = time.perf_counter()
                writer.flush(timeout_s=5.0)  # 等写线程消化完本条（确认被丢弃）
                assert (time.perf_counter() - start) * 1000 < 100

        assert writer.dropped == 100
        assert writer.written == 1  # 成功条目不受影响
        # 连续 100 条失败 → ERROR 告警一条（不阻塞主路径）
        assert any("台账" in r.message for r in caplog.records if r.levelno >= logging.ERROR)
        # 丢弃的条目不落盘；检索器对占位目录不崩溃
        assert query.query("req-fail") == []

    def test_recovers_after_failure(self, writer, tmp_path):
        ledger_file = tmp_path / "jev-ledger.jsonl"
        writer.flush(timeout_s=5.0)  # 确认无在途条目（文件尚未创建）
        ledger_file.mkdir()  # 同名目录 → 后续 open(a) 真实失败
        writer.log(decision_entry("req-f1"))
        writer.flush(timeout_s=5.0)
        assert writer.dropped == 1

        # 修复磁盘（目录移除，恢复文件可写）→ 后续写入恢复
        ledger_file.rmdir()
        writer.log(decision_entry("req-f2"))
        writer.flush(timeout_s=5.0)
        assert writer.written == 1
        assert writer.dropped == 1


class TestExport:
    def test_export_jsonl_filtered_by_request(self, writer, query, tmp_path):
        writer.log(decision_entry("req-a", point_id="p1"))
        writer.log(decision_entry("req-b", point_id="p2"))
        writer.log(decision_entry("req-a", point_id="p3"))
        writer.close()

        out = tmp_path / "export.jsonl"
        count = query.export_jsonl("req-a", out)
        assert count == 2
        lines = out.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        payloads = [json.loads(line) for line in lines]
        assert {p["request_id"] for p in payloads} == {"req-a"}

    def test_export_missing_request_writes_empty(self, query, tmp_path):
        out = tmp_path / "empty.jsonl"
        assert query.export_jsonl("req-none", out) == 0
        assert out.read_text(encoding="utf-8") == ""


class TestLifecycle:
    def test_close_idempotent_and_context_manager(self, tmp_path):
        with LedgerWriter(dir=tmp_path) as writer:
            writer.log(decision_entry("req-ctx"))
        writer.close()  # 幂等
        assert writer.written == 1
        assert (tmp_path / "jev-ledger.jsonl").read_text(encoding="utf-8").count("\n") == 1

    def test_query_reads_multiple_files_concatenated(self, tmp_path):
        """跨文件（分段导出/多日）目录全量检索。"""
        with LedgerWriter(dir=tmp_path, filename="day1.jsonl") as w1:
            w1.log(decision_entry("req-m"))
        with LedgerWriter(dir=tmp_path, filename="day2.jsonl") as w2:
            w2.log(decision_entry("req-m", point_id="p2"))
        assert len(LedgerQuery(dir=tmp_path).query("req-m")) == 2
