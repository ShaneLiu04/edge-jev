"""T021：基准执行器与报告——真实组件端到端（FR13）。

约束 C1：禁 mock。小脑为真实契约微型 bundle（conftest）；大脑为标准库
http.server 实现的真实 OpenAI 协议对端（test_chat_cascade 的 _BrainServer
模式）；级联/复核/开销剖析全部真实前向。真实资产全量基准（BS-05 验收门）
见 tests/integration（assets+slow）。
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from typer.testing import CliRunner

from jev.bench.report import render_markdown, write_reports
from jev.bench.runner import BenchQuestion, load_bench_set, run_bench, validate_bench_set
from jev.cli.main import app as cli_app
from jev.errors import JevError
from jev.kernel.cerebellum.laya_onnx import LayaOnnxBackend
from jev.kernel.cerebrum.llama_server import LlamaServerClient
from jev.kernel.decisions.framework import DecisionPoint, DecisionRegistry, Stage

runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parents[2]


def _completion(content: str, *, completion_tokens: int = 128, prompt_tokens: int = 10) -> dict:
    return {
        "id": "chatcmpl-srv",
        "object": "chat.completion",
        "model": "local",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


class _BrainHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        pass

    def do_POST(self):
        server = self.server
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        server.requests.append({"path": self.path, "body": body})
        if self.path != "/v1/chat/completions":
            self._send_json(404, {"error": "not found"})
        else:
            self._send_json(200, server.canned[0])

    def _send_json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class _BrainServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address) -> None:
        super().__init__(address, _BrainHandler)
        self.requests: list[dict] = []
        self.canned: list[dict] = [_completion("基线大脑的完整回答，足够切题且完整。")]


@pytest.fixture()
def brain_server():
    server = _BrainServer(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture()
def brain(brain_server):
    return LlamaServerClient(base_url=f"http://127.0.0.1:{brain_server.server_address[1]}")


@pytest.fixture()
def cerebellum(model_path, tokenizer_path, config_path):
    return LayaOnnxBackend(
        model_path=model_path, tokenizer_path=tokenizer_path, config_path=config_path
    )


def _point(point_id: str, stage: Stage, options: tuple[str, ...], *, tau: float) -> DecisionPoint:
    return DecisionPoint(
        id=point_id, stage=stage, question=f"{point_id}: {options[0]} or {options[-1]}?",
        tau=tau, fallback_action=options[-1], question_type="choice", options=options,
    )


def _bench_registry() -> DecisionRegistry:
    """direct 路由注册表 + POST 复核点（no_retry/complete/matches 通过侧双保险）。"""
    registry = DecisionRegistry()
    registry.register(_point("intent", Stage.PRE, ("simple", "decision"), tau=0.7))
    registry.register(_point("safety", Stage.PRE, ("unsafe", "safe"), tau=0.8))
    registry.register(_point("needs_retry", Stage.POST, ("retry", "no_retry"), tau=0.6))
    registry.register(_point("answer_complete", Stage.POST, ("incomplete", "complete"), tau=0.7))
    registry.register(_point("matches_request", Stage.POST, ("off_topic", "matches"), tau=0.7))
    return registry


def _questions(n: int = 4) -> list[BenchQuestion]:
    return [
        BenchQuestion(
            id=f"q-{i}",
            category="simple" if i % 2 == 0 else "complex",
            expect_route="direct",
            messages=({"role": "user", "content": f"基准问题 {i}"},),
        )
        for i in range(n)
    ]


class TestRunBench:
    def test_full_run_cascade_vs_baseline_with_post_review(
        self, cerebellum, brain, brain_server
    ):
        result = run_bench(
            cerebellum=cerebellum,
            registry=_bench_registry(),
            brain=brain,
            questions=_questions(4),
            include_baseline=True,
            overhead_repeats=3,
        )
        # 级联侧：全部 direct（真前向路由）且成功
        assert all(r.ok for r in result.results)
        assert {r.route for r in result.results} == {"direct"}
        # 基线侧：真实大脑对端被调用且计入 token
        assert len(brain_server.requests) == 4
        assert all(r.baseline_ok for r in result.results)
        assert all(r.baseline_tokens == 138 for r in result.results)
        # direct 不产 token；POST 复核双侧有值（同一把尺子）
        assert all(r.tokens == 0 for r in result.results)
        assert all(r.cascade_accepted is not None for r in result.results)
        assert all(r.baseline_accepted is not None for r in result.results)
        # 五类指标齐备
        assert result.routes["counts"]["direct"] == 4
        assert result.routes["hit_rate"] == 1.0
        assert result.latency["cascade"]["n"] == 4
        assert result.latency["baseline_brain"]["n"] == 4
        assert result.precision["delta_pt"] is not None
        assert result.cost["baseline_total_tokens"] == 4 * 138
        assert result.cost["savings_ratio"] == 1.0
        # 决策点开销：PRE 2 问批 ×3 次真前向
        assert result.decision_overhead["n"] == 3
        assert result.decision_overhead["batch_questions"] == 2
        assert result.decision_overhead["p50"] > 0
        # 决策面快照
        assert result.stage_counts["PRE"] == {"active": 2, "shadow": 0}
        assert result.stage_counts["POST"] == {"active": 3, "shadow": 0}
        # BS-05 门结构（数值不做确定性断言——微型模型延迟语义不同于生产）
        assert set(result.gates) == {
            "hit_rate", "hit_rate_pass", "direct_p50_ms",
            "brain_path_p50_ms", "latency_pass", "pass",
        }
        assert result.gates["hit_rate_pass"] is True

    def test_quick_mode_skips_baseline(self, cerebellum):
        result = run_bench(
            cerebellum=cerebellum,
            registry=_bench_registry(),
            brain=None,
            questions=_questions(2),
            include_baseline=False,
        )
        assert all(r.baseline_ok is False for r in result.results)
        assert all(r.baseline_accepted is None for r in result.results)
        assert result.latency["baseline_brain"]["n"] == 0
        assert result.gates["brain_path_p50_ms"] is None


class TestReports:
    def test_write_reports_json_and_markdown(self, cerebellum, brain, tmp_path):
        result = run_bench(
            cerebellum=cerebellum,
            registry=_bench_registry(),
            brain=brain,
            questions=_questions(3),
            include_baseline=True,
            overhead_repeats=2,
        )
        paths = write_reports(result, tmp_path)
        payload = json.loads(paths["json"].read_text(encoding="utf-8"))
        assert payload["kind"] == "jev-bench-report"
        assert len(payload["questions"]) == 3
        assert payload["gates"]["hit_rate"] == 1.0
        assert payload["created"]  # ISO 时间戳
        markdown = paths["markdown"].read_text(encoding="utf-8")
        assert "# Jev 基准报告" in markdown
        assert "## 路由分布与命中率" in markdown
        assert "## 延迟分布" in markdown
        assert "## 精度保持" in markdown
        assert "## 成本" in markdown
        assert "## 决策点开销" in markdown
        assert "## BS-05 验收门" in markdown

    def test_render_markdown_handles_empty_fields(self, cerebellum):
        result = run_bench(
            cerebellum=cerebellum,
            registry=_bench_registry(),
            brain=None,
            questions=_questions(2),
            include_baseline=False,
        )
        markdown = render_markdown(result)
        assert "—" in markdown  # 空基线字段以 — 呈现
        assert "PASS" in markdown or "FAIL" in markdown


class TestLoadBenchSet:
    def test_loads_real_repo_bench_set_with_stratification(self):
        questions = load_bench_set(REPO_ROOT / "configs" / "bench-set.yaml")
        assert len(questions) == 40
        simple = [q for q in questions if q.category == "simple"]
        complex_ = [q for q in questions if q.category == "complex"]
        assert len(simple) == 28 and len(complex_) == 12  # 7:3 分层
        assert {q.expect_route for q in questions} <= {"direct", "light", "full"}
        assert all(q.messages for q in questions)

    def test_v2_is_bilingual_with_balanced_languages(self):
        questions = load_bench_set(REPO_ROOT / "configs" / "bench-set.yaml")
        counts = validate_bench_set(questions)
        assert counts == {"zh": 20, "en": 20}

    def test_v2_each_language_covers_all_routes(self):
        questions = load_bench_set(REPO_ROOT / "configs" / "bench-set.yaml")
        for lang in ("zh", "en"):
            routes = {q.expect_route for q in questions if q.lang == lang}
            assert routes == {"direct", "light", "full"}

    def test_v2_multi_turn_questions_are_labeled(self):
        questions = load_bench_set(REPO_ROOT / "configs" / "bench-set.yaml")
        multi = [q for q in questions if q.turns > 1]
        assert multi and all(len(q.messages) >= 3 for q in multi)

    def test_validate_rejects_duplicate_ids(self):
        questions = load_bench_set(REPO_ROOT / "configs" / "bench-set.yaml")
        with pytest.raises(JevError, match="id 重复"):
            validate_bench_set([questions[0], questions[0]], require_bilingual=False)

    def test_validate_rejects_language_imbalance(self):
        questions = load_bench_set(REPO_ROOT / "configs" / "bench-set.yaml")
        zh_only = [q for q in questions if q.lang == "zh"]
        with pytest.raises(JevError, match="双语失衡"):
            validate_bench_set(zh_only)

    def test_v2_requires_lang_label(self, tmp_path):
        bad = tmp_path / "v2-no-lang.yaml"
        bad.write_text(
            "version: 2\nquestions:\n  - id: q\n    category: simple\n"
            "    expect_route: direct\n    messages: [{role: user, content: hi}]\n",
            encoding="utf-8",
        )
        with pytest.raises(JevError, match="lang 非法"):
            load_bench_set(bad)

    def test_v1_without_lang_defaults_to_chinese(self, tmp_path):
        legacy = tmp_path / "v1.yaml"
        legacy.write_text(
            "version: 1\nquestions:\n  - id: q\n    category: simple\n"
            "    expect_route: direct\n    messages: [{role: user, content: hi}]\n",
            encoding="utf-8",
        )
        loaded = load_bench_set(legacy)
        assert loaded[0].lang == "zh" and loaded[0].turns == 1

    def test_rejects_unknown_version(self, tmp_path):
        bad = tmp_path / "v9.yaml"
        bad.write_text(
            "version: 9\nquestions:\n  - id: q\n    category: simple\n"
            "    expect_route: direct\n    messages: [{role: user}]\n",
            encoding="utf-8",
        )
        with pytest.raises(JevError, match="版本不支持"):
            load_bench_set(bad)

    def test_rejects_missing_and_malformed(self, tmp_path):
        with pytest.raises(JevError, match="不存在"):
            load_bench_set(tmp_path / "nope.yaml")
        bad = tmp_path / "bad.yaml"
        bad.write_text("questions: []", encoding="utf-8")
        with pytest.raises(JevError, match="为空"):
            load_bench_set(bad)
        bad2 = tmp_path / "bad2.yaml"
        bad2.write_text(
            "questions:\n  - id: q\n    category: simple\n"
            "    expect_route: teleport\n    messages: [{role: user}]\n",
            encoding="utf-8",
        )
        with pytest.raises(JevError, match="expect_route 非法"):
            load_bench_set(bad2)


class TestBenchCli:
    def test_bench_help_exits_zero_and_shows_options(self):
        result = runner.invoke(cli_app, ["bench", "--help"])
        assert result.exit_code == 0
        for option in ("--full", "--set", "--out"):
            assert option in result.output
