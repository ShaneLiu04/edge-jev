"""基准执行器（design.md §4.4 bench/runner.py，FR13，T021）。

真实组件端到端：级联侧走真实 chat 级联（FastAPI TestClient ASGI 往返，
同 repo 先例 test_systemone_endpoint）；基线侧直连真实大脑客户端
（LlamaServerClient，不经级联）；两侧答案用同一把尺子复核（POST 决策
点真前向 + CascadeEngine.handle_post），保证精度保持指标可比。
"""

import dataclasses
import json
import logging
import time
from pathlib import Path

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jev.bench.metrics import (
    bs05_gates,
    cost_summary,
    latency_summary,
    precision_delta_pt,
    route_summary,
)
from jev.errors import JevError
from jev.kernel.cascade.engine import CascadeEngine, PostVerdict
from jev.kernel.cerebellum.base import CerebellumBackend
from jev.kernel.cerebrum.llama_server import LlamaServerClient
from jev.kernel.decisions.framework import DecisionRegistry, Stage
from jev.server.chat import create_chat_router, evaluate_decisions

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class BenchQuestion:
    """基准问题：分层标注（category）+ 期望路径（expect_route）+ 语种/轮数（v2）。"""

    id: str
    category: str
    expect_route: str
    messages: tuple[dict, ...]
    lang: str = "zh"
    turns: int = 1


@dataclasses.dataclass(frozen=True)
class QuestionResult:
    """单题结果：级联 + 直连大脑基线 + 双侧复核。"""

    question_id: str
    category: str
    expect_route: str
    route: str | None
    ok: bool
    latency_ms: float
    tokens: int
    cascade_accepted: bool | None
    baseline_ok: bool
    baseline_latency_ms: float
    baseline_tokens: int
    baseline_accepted: bool | None


@dataclasses.dataclass(frozen=True)
class BenchResult:
    """基准总结果：逐题 + 决策点开销 + 聚合指标 + BS-05 验收门。"""

    results: tuple[QuestionResult, ...]
    decision_overhead: dict
    stage_counts: dict
    latency: dict
    routes: dict
    precision: dict
    cost: dict
    gates: dict

    def to_dict(self) -> dict:
        return {
            "kind": "jev-bench-report",
            "questions": [dataclasses.asdict(r) for r in self.results],
            "decision_overhead": self.decision_overhead,
            "stage_counts": self.stage_counts,
            "latency": self.latency,
            "routes": self.routes,
            "precision": self.precision,
            "cost": self.cost,
            "gates": self.gates,
        }


def load_bench_set(path: Path | str) -> list[BenchQuestion]:
    """加载基准问题集（YAML，v1 兼容/v2 双语）。"""
    file = Path(path)
    if not file.is_file():
        raise JevError(f"基准问题集不存在: {file}")
    try:
        payload = yaml.safe_load(file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise JevError(f"基准问题集不是合法 YAML（{file}）: {exc}") from exc
    questions_raw = payload.get("questions") if isinstance(payload, dict) else None
    if not isinstance(questions_raw, list) or not questions_raw:
        raise JevError(f"基准问题集为空（{file}）")
    version = payload.get("version", 1)
    if version not in (1, 2):
        raise JevError(f"基准问题集版本不支持（{file}）: {version!r}")

    questions: list[BenchQuestion] = []
    for index, item in enumerate(questions_raw):
        if not isinstance(item, dict):
            raise JevError(f"基准问题 #{index} 不是对象: {item!r}")
        messages = item.get("messages")
        expect = item.get("expect_route")
        category = item.get("category")
        # v1 无 lang 标注（中文集）；v2 必须显式标注
        lang = item.get("lang", "zh" if version == 1 else None)
        turns = item.get("turns", 1)
        if not isinstance(messages, list) or not messages:
            raise JevError(f"基准问题 #{index} 缺少 messages")
        if expect not in ("direct", "light", "full"):
            raise JevError(f"基准问题 #{index} expect_route 非法: {expect!r}")
        if category not in ("simple", "complex"):
            raise JevError(f"基准问题 #{index} category 非法: {category!r}")
        if lang not in ("zh", "en"):
            raise JevError(f"基准问题 #{index} lang 非法: {lang!r}")
        if isinstance(turns, bool) or not isinstance(turns, int) or turns < 1:
            raise JevError(f"基准问题 #{index} turns 非法: {turns!r}")
        questions.append(
            BenchQuestion(
                id=str(item.get("id", f"q-{index}")),
                category=category,
                expect_route=expect,
                messages=tuple(messages),
                lang=lang,
                turns=turns,
            )
        )
    return questions


def validate_bench_set(
    questions: list[BenchQuestion],
    *,
    require_bilingual: bool = True,
    min_lang_share: float = 0.4,
) -> dict[str, int]:
    """校验问题集：id 唯一 + 双语占比阈值；返回语种计数（供报告指纹）。"""
    ids = [question.id for question in questions]
    duplicated = sorted({qid for qid in ids if ids.count(qid) > 1})
    if duplicated:
        raise JevError(f"基准问题集 id 重复: {duplicated}")
    counts: dict[str, int] = {}
    for question in questions:
        counts[question.lang] = counts.get(question.lang, 0) + 1
    if require_bilingual:
        total = len(questions)
        for lang in ("zh", "en"):
            if counts.get(lang, 0) / total < min_lang_share:
                raise JevError(
                    f"基准问题集双语失衡: {lang}={counts.get(lang, 0)}/{total} "
                    f"< {min_lang_share:.0%}"
                )
    return counts


def run_bench(
    *,
    cerebellum: CerebellumBackend,
    registry: DecisionRegistry,
    brain: LlamaServerClient | None,
    questions: list[BenchQuestion],
    max_tokens: int = 256,
    overhead_repeats: int = 5,
    include_baseline: bool = True,
) -> BenchResult:
    """执行基准：逐题 级联 vs 直连大脑 + 决策点开销剖析。"""
    engine = CascadeEngine(registry)
    app = FastAPI()
    app.include_router(
        create_chat_router(cerebellum, brain, registry, engine=engine)
    )
    client = TestClient(app)

    results: list[QuestionResult] = []
    for question in questions:
        messages = [dict(m) for m in question.messages]
        # 级联侧（真实 chat 级联 ASGI 往返）
        started = time.perf_counter()
        response = client.post(
            "/v1/chat/completions",
            json={"messages": messages, "max_tokens": max_tokens},
        )
        latency_ms = (time.perf_counter() - started) * 1000
        ok = response.status_code == 200
        route = response.headers.get("x-jev-route") if ok else None
        cascade_answer = ""
        tokens = 0
        if ok:
            body = response.json()
            choices = body.get("choices") or [{}]
            cascade_answer = str(
                (choices[0].get("message") or {}).get("content", "")
            )
            usage = body.get("usage") or {}
            tokens = int(usage.get("prompt_tokens", 0)) + int(
                usage.get("completion_tokens", 0)
            )
        # 直连大脑基线（不经级联）
        baseline_ok = False
        baseline_latency_ms = 0.0
        baseline_tokens = 0
        baseline_answer = ""
        if include_baseline and brain is not None:
            started = time.perf_counter()
            try:
                data = brain.chat(messages, max_tokens=max_tokens)
            except JevError as exc:
                logger.warning("基线大脑调用失败（%s）: %s", question.id, exc)
                data = None
            baseline_latency_ms = (time.perf_counter() - started) * 1000
            if data is not None:
                baseline_ok = True
                baseline_answer = str(
                    data["choices"][0]["message"]["content"]
                )
                usage = data.get("usage") or {}
                baseline_tokens = int(usage.get("prompt_tokens", 0)) + int(
                    usage.get("completion_tokens", 0)
                )
        # 同一把尺子：POST 复核双侧答案（真实决策点前向）
        cascade_accepted = (
            _post_review(cerebellum, engine, registry, messages, cascade_answer)
            if cascade_answer else None
        )
        baseline_accepted = (
            _post_review(cerebellum, engine, registry, messages, baseline_answer)
            if baseline_answer else None
        )
        results.append(
            QuestionResult(
                question_id=question.id,
                category=question.category,
                expect_route=question.expect_route,
                route=route,
                ok=ok,
                latency_ms=round(latency_ms, 3),
                tokens=tokens,
                cascade_accepted=cascade_accepted,
                baseline_ok=baseline_ok,
                baseline_latency_ms=round(baseline_latency_ms, 3),
                baseline_tokens=baseline_tokens,
                baseline_accepted=baseline_accepted,
            )
        )

    routes = [r.route for r in results]
    latencies = [r.latency_ms for r in results]
    # 仅统计真实发生的基线调用（quick 模式/单题失败不产生 0ms 假样本）
    baseline_latencies = [r.baseline_latency_ms for r in results if r.baseline_ok]
    cascade_tokens = [r.tokens for r in results]
    baseline_tokens_list = [r.baseline_tokens for r in results]

    by_route_latencies: dict[str, list[float]] = {}
    for route, latency in zip(routes, latencies, strict=True):
        if route is not None:
            by_route_latencies.setdefault(route, []).append(latency)

    overhead = _decision_overhead(
        cerebellum, registry, questions, repeats=overhead_repeats
    )
    precision_delta = precision_delta_pt(
        [r.cascade_accepted for r in results],
        [r.baseline_accepted for r in results],
    )
    from jev.bench.metrics import acceptance_rate

    return BenchResult(
        results=tuple(results),
        decision_overhead=overhead,
        stage_counts=_stage_counts(registry),
        latency={
            "cascade": latency_summary(latencies),
            "baseline_brain": latency_summary(baseline_latencies),
            "by_route": {
                route: latency_summary(values)
                for route, values in sorted(by_route_latencies.items())
            },
        },
        routes=route_summary(routes),
        precision={
            "cascade_acceptance": acceptance_rate(
                [r.cascade_accepted for r in results]
            ),
            "baseline_acceptance": acceptance_rate(
                [r.baseline_accepted for r in results]
            ),
            "delta_pt": precision_delta,
        },
        cost=cost_summary(cascade_tokens, baseline_tokens_list, routes),
        gates=bs05_gates(routes, latencies, baseline_latencies),
    )


def _post_review(
    cerebellum: CerebellumBackend,
    engine: CascadeEngine,
    registry: DecisionRegistry,
    messages: list[dict],
    answer: str,
) -> bool | None:
    """POST 复核一把尺子：真决策点批量前向 + handle_post；无 POST 点 → None。"""
    post_points = registry.active(Stage.POST)
    if not post_points:
        return None
    state = json.dumps(
        messages + [{"role": "assistant", "content": answer}],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    decisions, _, _ = evaluate_decisions(cerebellum, post_points, state)
    verdict = engine.handle_post(answer, decisions)
    return verdict is PostVerdict.ACCEPT


def _decision_overhead(
    cerebellum: CerebellumBackend,
    registry: DecisionRegistry,
    questions: list[BenchQuestion],
    *,
    repeats: int,
) -> dict:
    """决策点开销剖析：PRE active 批量单次前向真延迟（重复计时）。"""
    pre_points = registry.active(Stage.PRE)
    if not pre_points or not questions or repeats <= 0:
        return {"n": 0, "p50": None, "p95": None, "batch_questions": len(pre_points)}
    from jev.kernel.decisions.framework import to_question

    state = json.dumps(
        [dict(m) for m in questions[0].messages],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    questions_payload = {point.id: to_question(point) for point in pre_points}
    latencies: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter()
        cerebellum.system_one(state, questions_payload)
        latencies.append((time.perf_counter() - started) * 1000)
    summary = latency_summary(latencies)
    return {
        "n": summary["n"],
        "p50": summary["p50"],
        "p95": summary["p95"],
        "batch_questions": len(pre_points),
    }


def _stage_counts(registry: DecisionRegistry) -> dict:
    """逐 stage active/shadow 计数（决策面快照）。"""
    counts: dict[str, dict[str, int]] = {}
    for stage in Stage:
        counts[stage.value] = {
            "active": len(registry.active(stage)),
            "shadow": len(registry.shadow(stage)),
        }
    return counts


__all__ = [
    "BenchQuestion",
    "QuestionResult",
    "BenchResult",
    "load_bench_set",
    "run_bench",
]
