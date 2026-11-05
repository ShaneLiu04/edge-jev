"""AR023 MSPP：多槽持久化前缀快照真实冷重启并发实验（无 mock）。

每个模式先预热保存快照，再重启 llama-server，测冷启动第一轮与稳态第二轮。
对比：parallel1 / parallel4 auto / parallel4 scheduler / parallel4 scheduler+MSPP。
"""

from __future__ import annotations

import json
import os
import socket
import tempfile
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
from jev.kernel.cerebrum.snapshot import SnapshotClient, SnapshotConfig

REPO_ROOT = Path(__file__).resolve().parents[1]
CTX_SIZE = int(os.environ.get("JEV_AR023_CTX", "16384"))
GPU_LAYERS = int(os.environ.get("JEV_AR023_GPU_LAYERS", "99"))
PROBE_TIMEOUT_S = float(os.environ.get("JEV_AR023_PROBE_TIMEOUT", "180"))
REQUEST_TIMEOUT_S = float(os.environ.get("JEV_AR023_REQUEST_TIMEOUT", "600"))
CONCURRENCY = int(os.environ.get("JEV_AR023_CONCURRENCY", "4"))
ROUNDS = int(os.environ.get("JEV_AR023_ROUNDS", "2"))
MAX_TOKENS = int(os.environ.get("JEV_AR023_MAX_TOKENS", "16"))
QUESTION_COUNT = int(os.environ.get("JEV_AR023_QUESTIONS", "12"))


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _manager(parallel: int, snapshot_dir: Path | None) -> LlamaServerManager:
    if DEFAULT_CUDART.is_dir():
        os.environ["PATH"] = str(DEFAULT_CUDART) + os.pathsep + os.environ["PATH"]
    return LlamaServerManager(
        llama_server_bin=DEFAULT_LLAMA_BIN,
        gguf_path=DEFAULT_BRAIN_GGUF,
        port=_free_port(),
        ctx_size=CTX_SIZE,
        probe_timeout_s=PROBE_TIMEOUT_S,
        restart_max=0,
        extra_args=[
            "--n-gpu-layers",
            str(GPU_LAYERS),
            "--parallel",
            str(parallel),
        ],
        slot_save_path=snapshot_dir,
    )


def _client(
    manager: LlamaServerManager,
    *,
    parallel: int,
    scheduler: bool,
    snapshot_dir: Path | None,
) -> tuple[LlamaServerClient | ScheduledClient, SnapshotClient | None]:
    inner = LlamaServerClient(manager.base_url, timeout_s=REQUEST_TIMEOUT_S, cache_prompt=True)
    snap: SnapshotClient | None = None
    if snapshot_dir is not None:
        snap = SnapshotClient(
            inner,
            SnapshotConfig(
                enabled=True,
                directory=snapshot_dir,
                max_entries=8,
                min_chars=200,
                per_request_slot=scheduler,
            ),
            "ar023",
        )
        inner = snap  # type: ignore[assignment]
    if scheduler:
        client: LlamaServerClient | ScheduledClient = ScheduledClient(
            inner,  # type: ignore[arg-type]
            SlotScheduler(SlotSchedulerConfig(enabled=True, slots=parallel)),
        )
    else:
        client = inner
    return client, snap


def _round(
    client: LlamaServerClient | ScheduledClient,
    prompts: list[str],
) -> dict:
    timings: list[float] = []
    answers: list[str] = []
    prompt_n: list[int] = []
    cache_n: list[int] = []
    statuses: list[str] = []

    def call(prompt: str) -> tuple[float, str, dict, str]:
        started = time.perf_counter()
        response = client.chat(
            [{"role": "user", "content": prompt}],
            max_tokens=MAX_TOKENS,
            temperature=0.0,
            timeout_s=REQUEST_TIMEOUT_S,
        )
        elapsed = (time.perf_counter() - started) * 1000
        content = (response.get("choices") or [{}])[0].get("message", {}).get(
            "content", ""
        )
        timings_data = response.get("timings") or {}
        return elapsed, content, timings_data, str(response.get("_jev_snapshot", "off"))

    wall_started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        for elapsed, content, timings_data, status in pool.map(call, prompts):
            timings.append(elapsed)
            answers.append(content)
            prompt_n.append(int(timings_data.get("prompt_n", 0)))
            cache_n.append(int(timings_data.get("cache_n", 0)))
            statuses.append(status)
    wall_ms = (time.perf_counter() - wall_started) * 1000
    return {
        "wall_ms": round(wall_ms, 1),
        "p50_ms": round(sorted(timings)[len(timings) // 2], 1),
        "throughput_rps": round(len(prompts) / (wall_ms / 1000), 3),
        "prompt_n": prompt_n,
        "cache_n": cache_n,
        "snapshot": statuses,
        "restores": statuses.count("restore"),
        "answers": answers,
    }


def _wait_file(directory: Path, timeout_s: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if any(directory.glob("*.bin")):
            return True
        time.sleep(0.05)
    return any(directory.glob("*.bin"))


def _run_mode(
    name: str,
    *,
    parallel: int,
    scheduler: bool,
    snapshot: bool,
    prompts: list[str],
) -> dict:
    snapdir = Path(tempfile.mkdtemp(prefix=f"jev-ar023-{name}-")) if snapshot else None
    manager = _manager(parallel, snapdir)
    try:
        manager.start()
        client, snap = _client(
            manager,
            parallel=parallel,
            scheduler=scheduler,
            snapshot_dir=snapdir,
        )
        print(f"[{name}] warm", flush=True)
        warm = _round(client, prompts)
        if snap is not None and snapdir is not None:
            if not _wait_file(snapdir):
                raise RuntimeError(f"{name}: snapshot 未生成")
        del snap
    finally:
        manager.stop()

    manager = _manager(parallel, snapdir)
    try:
        manager.start()
        client, _snap = _client(
            manager,
            parallel=parallel,
            scheduler=scheduler,
            snapshot_dir=snapdir,
        )
        print(f"[{name}] cold", flush=True)
        cold = _round(client, prompts)
        print(f"[{name}] steady", flush=True)
        steady = _round(client, prompts)
    finally:
        manager.stop()
    return {
        "name": name,
        "parallel": parallel,
        "scheduler": scheduler,
        "snapshot": snapshot,
        "warm": {k: v for k, v in warm.items() if k != "answers"},
        "cold": cold,
        "steady": steady,
    }


def main() -> int:
    questions = build_shared_prefix(QUESTION_COUNT)
    prompts = [question.prompt for question in questions]
    modes = [
        ("parallel_1", 1, False, False),
        ("parallel_1_mspp", 1, False, True),
        ("parallel_4_auto", 4, False, False),
        ("parallel_4_scheduler", 4, True, False),
        ("parallel_4_mspp", 4, True, True),
    ]
    results = []
    for name, parallel, scheduler, snapshot in modes:
        print(f"[run] {name}", flush=True)
        results.append(
            _run_mode(
                name,
                parallel=parallel,
                scheduler=scheduler,
                snapshot=snapshot,
                prompts=prompts,
            )
        )

    baseline = next(item for item in results if item["name"] == "parallel_1")[
        "cold"
    ]["wall_ms"]
    auto = next(
        item for item in results if item["name"] == "parallel_4_auto"
    )["cold"]["wall_ms"]
    for item in results:
        cold = item["cold"]
        item["quality_ok"] = all(
            question.gold.value in answer
            for question, answer in zip(questions, cold["answers"], strict=False)
        )
        item["cold_speedup_vs_parallel1"] = round(baseline / cold["wall_ms"], 3)
        item["cold_speedup_vs_parallel4_auto"] = round(auto / cold["wall_ms"], 3)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = REPO_ROOT / "benchmarks" / "results" / f"ar023-mspp-{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "jev.bench.mspp/1",
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
        "# AR023 multi-slot persistent prefix (real cold restart)",
        "",
        "| mode | cold wall ms | cold p50 ms | restores | warm rps | steady p50 ms "
        "| vs p1 | vs p4 auto | quality |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in results:
        cold = item["cold"]
        warm = item["warm"]
        steady = item["steady"]
        lines.append(
            f"| {item['name']} | {cold['wall_ms']} | {cold['p50_ms']} | "
            f"{cold['restores']} | {warm['throughput_rps']} | {steady['p50_ms']} | "
            f"{item['cold_speedup_vs_parallel1']}x | "
            f"{item['cold_speedup_vs_parallel4_auto']}x | {item['quality_ok']} |"
        )
    lines.append(f"\nresults: {out / 'results.json'}")
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
