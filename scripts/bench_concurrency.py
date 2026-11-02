"""AR022 并行槽调度真实并发实验（真实 llama-server，无 mock）。

对照 parallel=1 / parallel=N（自动）/ parallel=N + 文档轮转 id_slot，
测量同一共享文档多问题的并发墙钟、吞吐与质量。
"""

from __future__ import annotations

import json
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from jev.bench.matrix import DEFAULT_BRAIN_GGUF, DEFAULT_CUDART, DEFAULT_LLAMA_BIN
from jev.bench.prefixset import build_shared_prefix
from jev.kernel.cerebrum.llama_server import LlamaServerClient, LlamaServerManager
from jev.kernel.cerebrum.scheduling import (
    ScheduledClient,
    SlotScheduler,
    SlotSchedulerConfig,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CTX_SIZE = 16384
GPU_LAYERS = 99
CONCURRENCY = 4
ROUNDS = 2


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _manager(parallel: int) -> LlamaServerManager:
    if DEFAULT_CUDART.is_dir():
        os.environ["PATH"] = str(DEFAULT_CUDART) + os.pathsep + os.environ["PATH"]
    manager = LlamaServerManager(
        llama_server_bin=DEFAULT_LLAMA_BIN,
        gguf_path=DEFAULT_BRAIN_GGUF,
        port=_free_port(),
        ctx_size=CTX_SIZE,
        probe_timeout_s=180.0,
        restart_max=0,
        extra_args=[
            "--n-gpu-layers",
            str(GPU_LAYERS),
            "--parallel",
            str(parallel),
        ],
    )
    manager.start()
    return manager


def _run_mode(name: str, parallel: int, scheduled: bool, prompts: list[str]) -> dict:
    manager = _manager(parallel)
    try:
        inner = LlamaServerClient(manager.base_url, timeout_s=300.0, cache_prompt=True)
        client = (
            ScheduledClient(
                inner,
                SlotScheduler(
                    SlotSchedulerConfig(enabled=True, slots=parallel)
                ),
            )
            if scheduled
            else inner
        )
        rounds: list[dict] = []
        for _round in range(ROUNDS):
            timings: list[float] = []
            answers: list[str] = []

            def call(prompt: str) -> tuple[float, str]:
                started = time.perf_counter()
                response = client.chat(
                    [{"role": "user", "content": prompt}],
                    max_tokens=16,
                    temperature=0.0,
                    timeout_s=300.0,
                )
                elapsed = (time.perf_counter() - started) * 1000
                content = (
                    response.get("choices") or [{}]
                )[0].get("message", {}).get("content", "")
                return elapsed, content

            wall_started = time.perf_counter()
            with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
                for elapsed, content in pool.map(call, prompts):
                    timings.append(elapsed)
                    answers.append(content)
            wall_ms = (time.perf_counter() - wall_started) * 1000
            rounds.append(
                {
                    "wall_ms": round(wall_ms, 1),
                    "p50_ms": round(sorted(timings)[len(timings) // 2], 1),
                    "throughput_rps": round(len(prompts) / (wall_ms / 1000), 3),
                    "answers": answers,
                }
            )
        return {
            "name": name,
            "parallel": parallel,
            "scheduled": scheduled,
            "rounds": rounds,
            "last": rounds[-1],
        }
    finally:
        manager.stop()


def main() -> int:
    questions = build_shared_prefix(12)
    prompts = [question.prompt for question in questions]
    modes = [
        ("parallel_1", 1, False),
        ("parallel_2_auto", 2, False),
        ("parallel_2_scheduled", 2, True),
        ("parallel_4_auto", 4, False),
        ("parallel_4_scheduled", 4, True),
    ]
    results = []
    for name, parallel, scheduled in modes:
        print(f"[run] {name}", flush=True)
        results.append(_run_mode(name, parallel, scheduled, prompts))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = REPO_ROOT / "benchmarks" / "results" / f"ar022-concurrency-{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    baseline = results[0]["last"]["wall_ms"]
    for result in results:
        result["speedup_vs_parallel1"] = round(
            baseline / result["last"]["wall_ms"], 3
        )
        result["quality_ok"] = all(
            question.gold.value in answer
            for question, answer in zip(
                questions, result["last"]["answers"], strict=False
            )
        )
    payload = {
        "schema": "jev.bench.concurrency/1",
        "created": stamp,
        "ctx_size": CTX_SIZE,
        "gpu_layers": GPU_LAYERS,
        "client_concurrency": CONCURRENCY,
        "rounds": ROUNDS,
        "results": results,
    }
    (out / "results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# AR022 parallel slot concurrency (real)",
        "",
        "| mode | wall ms | p50 ms | req/s | speedup | quality |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for result in results:
        last = result["last"]
        lines.append(
            f"| {result['name']} | {last['wall_ms']} | {last['p50_ms']} | "
            f"{last['throughput_rps']} | {result['speedup_vs_parallel1']}x | "
            f"{result['quality_ok']} |"
        )
    lines.append(f"\nresults: {out / 'results.json'}")
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
