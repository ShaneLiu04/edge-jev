"""AR019 PPKS 冷重启真实实验（真实 llama-server，无 mock）。

对照：
1) baseline：无快照目录，进程启动后首个同文档新问题（完整 prefill）；
2) PPKS：先写入同文档快照，重启进程后恢复快照，再问同文档新问题。

用法：
    uv run python scripts/bench_ppks.py
"""

from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path

from jev.bench.matrix import DEFAULT_BRAIN_GGUF, DEFAULT_CUDART, DEFAULT_LLAMA_BIN
from jev.bench.prefixset import build_shared_prefix
from jev.errors import JevError
from jev.kernel.cerebrum.llama_server import LlamaServerClient, LlamaServerManager
from jev.kernel.cerebrum.snapshot import SnapshotClient, SnapshotConfig, document_key
from jev.kernel.cerebrum.tiering import split_question_document

REPO_ROOT = Path(__file__).resolve().parents[1]
CTX_SIZE = 8192
GPU_LAYERS = 99
NAMESPACE = "ar019-ppks-large"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _manager(slot_dir: Path | None) -> LlamaServerManager:
    if DEFAULT_CUDART.is_dir():
        os.environ["PATH"] = str(DEFAULT_CUDART) + os.pathsep + os.environ["PATH"]
    manager = LlamaServerManager(
        llama_server_bin=DEFAULT_LLAMA_BIN,
        gguf_path=DEFAULT_BRAIN_GGUF,
        port=_free_port(),
        ctx_size=CTX_SIZE,
        probe_timeout_s=180.0,
        restart_max=0,
        extra_args=["--n-gpu-layers", str(GPU_LAYERS)],
        slot_save_path=slot_dir,
    )
    manager.start()
    return manager


def _ask(client: LlamaServerClient, prompt: str) -> dict:
    started = time.perf_counter()
    response = client.chat(
        [{"role": "user", "content": prompt}],
        max_tokens=32,
        temperature=0.0,
        timeout_s=300.0,
    )
    wall_ms = (time.perf_counter() - started) * 1000
    timings = response.get("timings") or {}
    return {
        "wall_ms": round(wall_ms, 1),
        "prompt_n": int(timings.get("prompt_n", 0)),
        "cache_n": int(timings.get("cache_n", 0)),
        "prompt_ms": round(float(timings.get("prompt_ms", 0.0)), 1),
        "predicted_ms": round(float(timings.get("predicted_ms", 0.0)), 1),
        "snapshot": response.get("_jev_snapshot"),
        "content": (response.get("choices") or [{}])[0].get("message", {}).get("content", ""),
    }


def _wait_file(path: Path, timeout_s: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_s
    stable_size = -1
    stable_since = time.monotonic()
    while time.monotonic() < deadline:
        if path.exists():
            size = path.stat().st_size
            if size == stable_size:
                if time.monotonic() - stable_since >= 0.5:
                    return
            else:
                stable_size = size
                stable_since = time.monotonic()
        time.sleep(0.1)
    raise JevError(f"快照未在 {timeout_s}s 内写稳: {path}")


def run_baseline(prompt: str) -> dict:
    manager = _manager(None)
    try:
        client = LlamaServerClient(manager.base_url, timeout_s=300.0, cache_prompt=True)
        return _ask(client, prompt)
    finally:
        manager.stop()


def run_ppks(prompt_seed: str, prompt_measured: str, slot_dir: Path) -> dict:
    document, _ = split_question_document(prompt_measured)
    key = document_key(
        [{"role": "user", "content": prompt_measured}], min_chars=1000
    )
    if key is None:
        raise JevError("测量 prompt 未识别出文档，无法生成快照键")
    filename = f"{NAMESPACE}-{key[:32]}.bin"
    path = slot_dir / filename

    manager = _manager(slot_dir)
    try:
        config = SnapshotConfig(
            enabled=True, directory=slot_dir, max_entries=4, min_chars=1000
        )
        client = SnapshotClient(
            LlamaServerClient(manager.base_url, timeout_s=300.0, cache_prompt=True),
            config,
            NAMESPACE,
        )
        seed_result = _ask(client, prompt_seed)
        _wait_file(path)
    finally:
        manager.stop()

    manager = _manager(slot_dir)
    try:
        config = SnapshotConfig(
            enabled=True, directory=slot_dir, max_entries=4, min_chars=1000
        )
        client = SnapshotClient(
            LlamaServerClient(manager.base_url, timeout_s=300.0, cache_prompt=True),
            config,
            NAMESPACE,
        )
        result = _ask(client, prompt_measured)
    finally:
        manager.stop()
    result["seed"] = {
        "prompt_n": seed_result["prompt_n"],
        "prompt_ms": seed_result["prompt_ms"],
        "snapshot": seed_result["snapshot"],
    }
    result["snapshot_file"] = filename
    result["snapshot_bytes"] = path.stat().st_size if path.exists() else 0
    result["document_chars"] = len(document)
    return result


def main() -> int:
    questions = build_shared_prefix(12)
    seed_prompt = questions[0].prompt
    measured_prompt = questions[1].prompt
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = REPO_ROOT / "benchmarks" / "results" / f"ar019-ppks-{stamp}"
    slot_dir = out / "kv_snapshots"
    slot_dir.mkdir(parents=True, exist_ok=True)

    print("[1/2] baseline cold start...", flush=True)
    baseline = run_baseline(measured_prompt)
    print("[2/2] PPKS save/restart/restore...", flush=True)
    ppks = run_ppks(seed_prompt, measured_prompt, slot_dir)

    ratio = (
        round(ppks["wall_ms"] / baseline["wall_ms"], 3)
        if baseline["wall_ms"] > 0
        else None
    )
    result = {
        "schema": "jev.bench.ppks/1",
        "created": stamp,
        "hardware_note": "Quadro RTX 5000 16GB (clock-limited) + local CPU",
        "ctx_size": CTX_SIZE,
        "gpu_layers": GPU_LAYERS,
        "baseline": baseline,
        "ppks": ppks,
        "wall_ratio": ratio,
        "restored_cache_tokens": ppks["cache_n"],
        "quality_ok": bool(questions[1].gold.value in ppks["content"]),
    }
    (out / "results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# AR019 PPKS cold-restart (real)",
        "",
        (
            f"- baseline wall: {baseline['wall_ms']} ms "
            f"(prompt_n={baseline['prompt_n']}, cache_n={baseline['cache_n']})"
        ),
        (
            f"- ppks wall: {ppks['wall_ms']} ms "
            f"(prompt_n={ppks['prompt_n']}, cache_n={ppks['cache_n']})"
        ),
        f"- ratio: {ratio}",
        f"- snapshot status: {ppks['snapshot']}",
        f"- snapshot bytes: {ppks['snapshot_bytes']}",
        f"- quality_ok: {result['quality_ok']}",
        f"- results: {out / 'results.json'}",
    ]
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
