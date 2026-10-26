"""真实实验矩阵（AR005 FR5-4/FR5-5）。

在真实 Laya + 真实 llama-server（可选真实 embedding）上评测 B0/B1/B2/AR005，
产出原始 JSON、Markdown 报告、风险/准入校准与可选 profile。

约束：后端不可用直接失败（退出码由 CLI 处理）；不生成任何伪造指标。
"""

from __future__ import annotations

import dataclasses
import json
import os
import time
import tomllib
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jev.bench.dataset import QualityQuestion, grade, load_cache_set, load_quality_set
from jev.bench.metrics import environment_fingerprint
from jev.bench.selector import ArmOutcome, select
from jev.errors import JevAssetError, JevError
from jev.kernel.cerebrum.embeddings import EmbeddingClient, EmbeddingService
from jev.kernel.cerebrum.llama_server import LlamaServerClient, LlamaServerManager
from jev.kernel.kernel import (
    Kernel,
    _build_admission,
    _build_evidence_config,
    _build_risk,
    _build_runtime,
)
from jev.kernel.prefill.evidence import EvidenceSelector
from jev.kernel.runtime.conformal import (
    calibrate_cache_admission,
    conformal_risk_threshold,
)
from jev.kernel.runtime.portfolio import to_toml
from jev.server.chat import create_chat_router

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_BASE_CONFIG = REPO_ROOT / "configs" / "jev.toml"
DEFAULT_QUALITY_SET = REPO_ROOT / "configs" / "quality-set.yaml"
DEFAULT_CACHE_SET = REPO_ROOT / "configs" / "cache-set.yaml"
DEFAULT_BRAIN_GGUF = (
    REPO_ROOT / "models" / "qwen" / "qwen2.5-1.5b-instruct-q4_k_m.gguf"
)
DEFAULT_LLAMA_BIN = REPO_ROOT / "models" / "llama-server" / "bin" / "llama-server.exe"
DEFAULT_CUDART = REPO_ROOT / "models" / "llama-server" / "cudart"

ARMS = ("B0", "B1", "B2", "AR005")

_ARM_OVERLAYS: dict[str, dict] = {
    "B0": {
        "runtime": {"enabled": False},
        "cache": {"enabled": False},
        "prefill": {"enabled": False},
        "cascade": {"capability_gate": False},
    },
    "B1": {
        "runtime": {"enabled": False},
        "cache": {"enabled": False},
        "prefill": {"enabled": False},
        "cascade": {"capability_gate": True},
    },
    "B2": {
        "runtime": {
            "enabled": True,
            "verifier_enabled": True,
            "self_verify_mode": "gate",
            "draft_injection": False,
            "memory_enabled": True,
            "keep_turns": 2,
        },
        "cache": {"enabled": True, "conformal_admission": False},
        "prefill": {"enabled": True},
        "cascade": {"capability_gate": True},
    },
    "AR005": {
        "runtime": {
            "enabled": True,
            "verifier_enabled": True,
            "self_verify_mode": "gate",
            "risk_control": True,
            "allocator_enabled": True,
            "memory_enabled": True,
            "draft_injection": False,
            "keep_turns": 2,
        },
        "cache": {"enabled": True, "conformal_admission": True},
        "prefill": {"enabled": True},
        "cascade": {"capability_gate": True},
    },
}


@dataclasses.dataclass(frozen=True)
class MatrixConfig:
    name: str
    seed: int
    lambda_cost: float
    limit: int
    arms: tuple[str, ...]
    brain: str
    brain_base_url: str
    embedding: str
    embedding_base_url: str
    gpu_layers: int
    ctx_size: int
    max_tokens: int
    timeout_s: float
    prompt_prefix: str
    repeat: int
    dataset: str
    overlays: dict


def load_matrix_config(path: Path | str) -> MatrixConfig:
    file = Path(path)
    try:
        data = tomllib.loads(file.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise JevError(f"实验配置不可用: {file}: {exc}") from exc
    arms = tuple(data.get("arms", ARMS))
    overlays = data.get("overlays", {}) or {}
    unknown = [arm for arm in arms if arm not in ARMS and arm not in overlays]
    if unknown:
        raise JevError(
            f"未知 arm: {unknown}（内置 {list(ARMS)}；组合需在 [overlays] 定义）"
        )
    return MatrixConfig(
        name=str(data.get("name", file.stem)),
        seed=int(data.get("seed", 20261026)),
        lambda_cost=float(data.get("lambda_cost", 0.15)),
        limit=int(data.get("limit", 0)),
        arms=arms,
        brain=str(data.get("brain", "spawn")),
        brain_base_url=str(data.get("brain_base_url", "")),
        embedding=str(data.get("embedding", "auto")),
        embedding_base_url=str(data.get("embedding_base_url", "")),
        gpu_layers=int(data.get("gpu_layers", 99)),
        ctx_size=int(data.get("ctx_size", 2048)),
        max_tokens=int(data.get("max_tokens", 64)),
        prompt_prefix=str(
            data.get("prompt_prefix", "写一段回答，并说明理由：")
        ),
        repeat=int(data.get("repeat", 1)),
        dataset=str(data.get("dataset", "quality")),
        timeout_s=float(data.get("timeout_s", 180.0)),
        overlays=overlays,
    )


def _load_questions(config: MatrixConfig):
    if config.dataset == "needles":
        from jev.bench.needles import build_needles

        return build_needles(30)
    if config.dataset != "quality":
        raise JevError(f"未知 dataset: {config.dataset}")
    return load_quality_set(DEFAULT_QUALITY_SET)


def _deep_merge(base: dict, overlay: dict) -> dict:
    merged = {key: dict(value) for key, value in base.items() if isinstance(value, dict)}
    for section, values in overlay.items():
        if not isinstance(values, dict):
            continue
        merged.setdefault(section, {}).update(values)
    return merged


def _arm_config(base: dict, arm: str, extra: dict | None = None) -> dict:
    preset = _ARM_OVERLAYS.get(arm, {})
    overlay = _deep_merge(preset, extra or {})
    return _deep_merge(base, overlay)


def _spawn_brain(config: MatrixConfig) -> LlamaServerManager:
    cudart = DEFAULT_CUDART
    if cudart.is_dir():
        os.environ["PATH"] = str(cudart) + os.pathsep + os.environ["PATH"]
    manager = LlamaServerManager(
        llama_server_bin=DEFAULT_LLAMA_BIN,
        gguf_path=DEFAULT_BRAIN_GGUF,
        port=_free_port(),
        ctx_size=config.ctx_size,
        probe_timeout_s=180.0,
        restart_max=0,
        extra_args=["--n-gpu-layers", str(config.gpu_layers)],
    )
    manager.start()
    return manager


def _spawn_embedding(config: MatrixConfig) -> EmbeddingService:
    if not DEFAULT_BRAIN_GGUF.is_file():
        raise JevAssetError(f"embedding 资产缺失: {DEFAULT_BRAIN_GGUF}")
    if DEFAULT_CUDART.is_dir():
        os.environ["PATH"] = str(DEFAULT_CUDART) + os.pathsep + os.environ["PATH"]
    port = _free_port()
    manager = LlamaServerManager(
        llama_server_bin=DEFAULT_LLAMA_BIN,
        gguf_path=DEFAULT_BRAIN_GGUF,
        port=port,
        ctx_size=4096,
        probe_timeout_s=180.0,
        restart_max=0,
        extra_args=[
            "--embeddings",
            "--pooling",
            "mean",
            "--batch-size",
            "2048",
            "--ubatch-size",
            "1024",
            "--n-gpu-layers",
            str(config.gpu_layers),
        ],
    )
    client = EmbeddingClient(f"http://127.0.0.1:{port}", timeout_s=25.0)
    service = EmbeddingService(manager, client)
    if not service.available():
        service.close()
        raise JevAssetError("真实 embedding 服务启动失败")
    return service


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _build_client(
    kernel: Kernel,
    brain: LlamaServerClient,
    config: dict,
    *,
    embedding_service: EmbeddingService | None,
    profile_name: str,
    force_full: bool = False,
    evidence: EvidenceSelector | None = None,
) -> TestClient:
    runtime_config, cache = _build_runtime(config)
    embeddings = embedding_service
    app = FastAPI()
    app.include_router(
        create_chat_router(
            kernel.cerebellum,
            brain,
            kernel.registry,
            engine=kernel.engine,
            route_policy=(lambda pre: "full") if force_full else None,
            runtime_config=runtime_config,
            cache=cache,
            embeddings=embeddings,
            risk_controller=_build_risk(config, REPO_ROOT / "configs"),
            admission_policy=_build_admission(config, REPO_ROOT / "configs"),
            profile_name=profile_name,
            evidence=evidence,
        )
    )
    return TestClient(app)


def _run_questions(
    client: TestClient,
    questions: list[QualityQuestion],
    *,
    arm: str,
    seed: int,
    max_tokens: int = 64,
    prompt_prefix: str = "",
    repeat: int = 1,
) -> list[dict]:
    rows: list[dict] = []
    for pass_index in range(max(1, repeat)):
        for question in questions:
            payload = {
                "model": "local",
                "messages": [
                    {"role": "user", "content": prompt_prefix + question.prompt}
                ],
                "temperature": 0.0,
                "max_tokens": max_tokens,
                "seed": seed,
            }
            started = time.perf_counter()
            response = client.post("/v1/chat/completions", json=payload)
            latency_ms = (time.perf_counter() - started) * 1000.0
            if response.status_code != 200:
                raise JevError(
                    f"矩阵请求失败 arm={arm} q={question.id}: "
                    f"HTTP {response.status_code} {response.text[:200]}"
                )
            body = response.json()
            content = str(body["choices"][0]["message"].get("content", ""))
            usage = body.get("usage") or {}
            headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower().startswith("x-jev")
            }
            rows.append(
                {
                    "arm": arm,
                    "question_id": question.id,
                    "pass": pass_index,
                    "category": question.category,
                    "lang": question.lang,
                    "quality": 1.0 if grade(content, question.gold) else 0.0,
                    "answer": content,
                    "prompt_tokens": int(usage.get("prompt_tokens", 0)),
                    "completion_tokens": int(usage.get("completion_tokens", 0)),
                    "latency_ms": round(latency_ms, 3),
                    "route": headers.get("x-jev-route"),
                    "u": _float_or_none(headers.get("x-jev-u")),
                    "headers": headers,
                }
            )
    return rows


def _float_or_none(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _calibrate_risk(
    b0: list[dict], b2: list[dict], out_dir: Path, alpha: float
) -> dict | None:
    b0_by_id = {
        row["question_id"]: row for row in b0 if row.get("pass", 0) == 0
    }
    scores: list[float] = []
    losses: list[float] = []
    for row in b2:
        if row.get("pass", 0) != 0:
            continue
        other = b0_by_id.get(row["question_id"])
        if other is None or row["u"] is None:
            continue
        scores.append(float(row["u"]))
        losses.append(max(0.0, float(other["quality"]) - float(row["quality"])))
    result = conformal_risk_threshold(scores, losses, alpha=alpha, direction="le")
    if not result.enabled:
        return None
    path = out_dir / "risk.json"
    path.write_text(json.dumps(result.stats(), ensure_ascii=False, indent=2), encoding="utf-8")
    return {"path": str(path), "result": result.stats()}


def _calibrate_cache(
    service: EmbeddingService, out_dir: Path, alpha: float
) -> dict | None:
    data = load_cache_set(DEFAULT_CACHE_SET)
    originals = [item.text for item in data.originals]
    vectors = service.embed(originals)

    def best_score(text: str) -> float:
        vector = service.embed([text])[0]
        return max(_cosine(vector, candidate) for candidate in vectors)

    positive = [best_score(item.text) for item in data.paraphrases]
    negative = [best_score(item.text) for item in data.negatives]
    policy = calibrate_cache_admission(positive, negative, alpha=alpha)
    if not policy.enabled:
        return None
    path = out_dir / "admission.json"
    path.write_text(json.dumps(policy.stats(), ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "path": str(path),
        "result": policy.stats(),
        "positive_scores": [round(value, 6) for value in positive],
        "negative_scores": [round(value, 6) for value in negative],
    }


def _cosine(first: list[float], second: list[float]) -> float:
    if not first or not second or len(first) != len(second):
        return 0.0
    dot = sum(a * b for a, b in zip(first, second, strict=True))
    left = sum(a * a for a in first) ** 0.5
    right = sum(b * b for b in second) ** 0.5
    if left == 0 or right == 0:
        return 0.0
    return dot / (left * right)


def run_matrix(
    config_path: Path | str,
    *,
    out_dir: Path | str | None = None,
    limit: int | None = None,
    write_profile: bool = False,
) -> dict:
    config = load_matrix_config(config_path)
    questions = _load_questions(config)
    limit = config.limit if limit is None else limit
    if limit > 0:
        questions = questions[:limit]
    started = time.time()
    run_name = f"{config.name}-{time.strftime('%Y%m%d-%H%M%S')}"
    out = Path(out_dir) if out_dir is not None else REPO_ROOT / "benchmarks" / "results" / run_name
    out.mkdir(parents=True, exist_ok=True)

    base = tomllib.loads(DEFAULT_BASE_CONFIG.read_text(encoding="utf-8"))
    kernel = Kernel.from_config(DEFAULT_BASE_CONFIG)
    brain_manager: LlamaServerManager | None = None
    embedding_service: EmbeddingService | None = None
    try:
        if config.brain == "external":
            if not config.brain_base_url:
                raise JevError("brain=external 但未配置 brain_base_url")
            brain = LlamaServerClient(
                config.brain_base_url,
                timeout_s=config.timeout_s,
                cache_prompt=False,
            )
            if not brain.health():
                raise JevError(f"外部大脑不可用: {config.brain_base_url}")
        else:
            brain = None  # 每个 arm 独立拉起，避免长跑后 llama-server 假死

        if config.embedding == "off":
            embedding_service = None
        elif config.embedding == "external":
            if not config.embedding_base_url:
                raise JevError("embedding=external 但未配置 embedding_base_url")
            embedding_service = EmbeddingService(
                None, EmbeddingClient(config.embedding_base_url, timeout_s=25.0)
            )
            if not embedding_service.available():
                raise JevError(f"外部 embedding 不可用: {config.embedding_base_url}")
        else:
            embedding_service = _spawn_embedding(config)

        extra = config.overlays or {}
        raw: dict[str, list[dict]] = {}
        for arm in config.arms:
            if arm == "AR005":
                continue
            arm_cfg = _arm_config(base, arm, extra.get(arm))
            arm_brain = brain
            arm_manager: LlamaServerManager | None = None
            if arm_brain is None:
                arm_manager = _spawn_brain(config)
                arm_brain = LlamaServerClient(
                    arm_manager.base_url,
                    timeout_s=config.timeout_s,
                    cache_prompt=False,
                )
            try:
                arm_prefill = arm_cfg.get("prefill", {}) or {}
                arm_cache = arm_cfg.get("cache", {}) or {}
                needs_embeddings = bool(arm_cache.get("enabled", False)) or bool(
                    arm_prefill.get("evidence", False)
                )
                arm_evidence = (
                    EvidenceSelector(
                        embedding_service.embed, _build_evidence_config(arm_cfg)
                    )
                    if (
                        embedding_service is not None
                        and bool(arm_prefill.get("evidence", False))
                    )
                    else None
                )
                client = _build_client(
                    kernel,
                    arm_brain,
                    arm_cfg,
                    embedding_service=(
                        embedding_service if needs_embeddings else None
                    ),
                    profile_name=arm,
                    force_full=(arm == "B0"),
                    evidence=arm_evidence,
                )
                raw[arm] = _run_questions(
                    client, questions, arm=arm, seed=config.seed,
                    max_tokens=config.max_tokens,
                    prompt_prefix=config.prompt_prefix,
                    repeat=config.repeat,
                )
            finally:
                if arm_manager is not None:
                    arm_manager.stop()
            (out / "progress.json").write_text(
                json.dumps(
                    {
                        "schema": "jev.matrix.progress/1",
                        "completed_arms": sorted(raw),
                        "remaining": [a for a in config.arms if a not in raw],
                        "metrics": _summarize(raw),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

        calibrations: dict[str, dict] = {}
        if "AR005" in config.arms:
            if "B0" in raw:
                risk = _calibrate_risk(
                    raw["B0"], raw.get("B2", raw[list(raw)[-1]]), out, alpha=0.10
                )
                if risk is not None:
                    calibrations["risk"] = risk
            if embedding_service is not None:
                admission = _calibrate_cache(embedding_service, out, alpha=0.10)
                if admission is not None:
                    calibrations["cache"] = admission
            ar_cfg = _arm_config(base, "AR005", extra.get("AR005"))
            if "risk" in calibrations:
                ar_cfg["runtime"]["risk_calibration"] = calibrations["risk"]["path"]
            if "cache" in calibrations:
                ar_cfg["cache"]["admission_calibration"] = calibrations["cache"]["path"]
            arm_brain = brain
            arm_manager = None
            if arm_brain is None:
                arm_manager = _spawn_brain(config)
                arm_brain = LlamaServerClient(
                    arm_manager.base_url,
                    timeout_s=config.timeout_s,
                    cache_prompt=False,
                )
            try:
                ar_prefill = ar_cfg.get("prefill", {}) or {}
                ar_evidence = (
                    EvidenceSelector(
                        embedding_service.embed, _build_evidence_config(ar_cfg)
                    )
                    if (
                        embedding_service is not None
                        and bool(ar_prefill.get("evidence", False))
                    )
                    else None
                )
                client = _build_client(
                    kernel,
                    arm_brain,
                    ar_cfg,
                    embedding_service=embedding_service,
                    profile_name="AR005",
                    evidence=ar_evidence,
                )
                raw["AR005"] = _run_questions(
                    client, questions, arm="AR005", seed=config.seed,
                    max_tokens=config.max_tokens,
                    prompt_prefix=config.prompt_prefix,
                    repeat=config.repeat,
                )
            finally:
                if arm_manager is not None:
                    arm_manager.stop()
            (out / "progress.json").write_text(
                json.dumps(
                    {
                        "schema": "jev.matrix.progress/1",
                        "completed_arms": sorted(raw),
                        "remaining": [],
                        "metrics": _summarize(raw),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        else:
            ar_cfg = _arm_config(base, "AR005", extra.get("AR005"))

        outcomes: list[ArmOutcome] = []
        for arm, rows in raw.items():
            for row in rows:
                outcomes.append(
                    ArmOutcome(
                        arm=arm,
                        question_id=row["question_id"],
                        quality=row["quality"],
                        tokens=row["completion_tokens"] + row["prompt_tokens"],
                        latency_ms=row["latency_ms"],
                        band=row["route"] or "unknown",
                    )
                )
        baseline_arm = (
            "B2" if "B2" in raw else ("B1" if "B1" in raw else (list(raw)[0] if raw else "B1"))
        )
        selection = select(
            outcomes,
            lambda_cost=config.lambda_cost,
            baseline_arm=baseline_arm,
            seed=config.seed,
            min_samples=min(20, len(questions)),
        )

        metrics = _summarize(raw)
        fingerprint = environment_fingerprint(
            extra={
                "arms": list(raw),
                "commands": [f"jev matrix --config {config_path}"],
                "run": run_name,
            }
        )
        payload = {
            "schema": "jev.matrix/1",
            "run": {
                "name": run_name,
                "started": started,
                "finished": time.time(),
                "seed": config.seed,
                "lambda_cost": config.lambda_cost,
                "limit": limit,
                "fingerprint": fingerprint,
            },
            "config": dataclasses.asdict(config),
            "calibrations": calibrations,
            "arms": raw,
            "metrics": metrics,
            "selection": dataclasses.asdict(selection),
        }
        (out / "results.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out / "report.md").write_text(
            _render_report(payload), encoding="utf-8"
        )
        if "AR005" in raw:
            profile_text = to_toml(
                "AR005",
                {
                    "runtime": {
                        "enabled": True,
                        "verifier_enabled": True,
                        "self_verify_mode": "gate",
                        "risk_control": "risk" in calibrations,
                        "risk_calibration": calibrations.get("risk", {}).get("path", ""),
                        "allocator_enabled": True,
                        "memory_enabled": True,
                        "keep_turns": 2,
                    },
                    "cache": {
                        "enabled": True,
                        "conformal_admission": "cache" in calibrations,
                        "admission_calibration": calibrations.get("cache", {}).get("path", ""),
                    },
                    "prefill": {"enabled": True},
                    "portfolio": {"name": "AR005"},
                },
            )
        else:
            chosen_cfg = _arm_config(
                base,
                selection.chosen_arm,
                extra.get(selection.chosen_arm),
            )
            profile_text = to_toml(
                selection.chosen_arm,
                {
                    section: chosen_cfg.get(section, {})
                    for section in ("runtime", "cache", "prefill")
                    if isinstance(chosen_cfg.get(section), dict)
                }
                | {"portfolio": {"name": selection.chosen_arm}},
            )
        (out / "profile.toml").write_text(profile_text, encoding="utf-8")
        if write_profile and selection.decision == "adopt":
            (REPO_ROOT / "configs" / "runtime-profile.toml").write_text(
                profile_text, encoding="utf-8"
            )
        return payload
    finally:
        kernel.close()
        if embedding_service is not None:
            embedding_service.close()
        if brain_manager is not None:
            brain_manager.stop()


def _summarize(raw: dict[str, list[dict]]) -> dict:
    metrics: dict[str, dict] = {}
    for arm, rows in raw.items():
        count = len(rows)
        if not count:
            continue
        metrics[arm] = {
            "count": count,
            "quality": round(sum(row["quality"] for row in rows) / count, 6),
            "mean_completion_tokens": round(
                sum(row["completion_tokens"] for row in rows) / count, 3
            ),
            "mean_prompt_tokens": round(
                sum(row["prompt_tokens"] for row in rows) / count, 3
            ),
            "mean_latency_ms": round(
                sum(row["latency_ms"] for row in rows) / count, 3
            ),
            "p50_latency_ms": sorted(row["latency_ms"] for row in rows)[count // 2],
            "routes": _counts(row["route"] for row in rows),
            "cache_hits": sum(
                1
                for row in rows
                if str(row["headers"].get("x-jev-cache", "")).startswith("hit-")
            ),
            "risk_denies": sum(
                1
                for row in rows
                if row["headers"].get("x-jev-risk") == "deny"
            ),
        }
    return metrics


def _counts(values) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        key = str(value)
        result[key] = result.get(key, 0) + 1
    return result


def _render_report(payload: dict) -> str:
    lines = [
        "# Jev 实验矩阵报告",
        "",
        f"- run: {payload['run']['name']}",
        f"- lambda_cost: {payload['run']['lambda_cost']}",
        f"- seed: {payload['run']['seed']}",
        f"- limit: {payload['run']['limit'] or 'all'}",
        "",
        "## 指标",
        "",
        "| arm | quality | completion tok | prompt tok | P50 ms | cache hits | risk denies |",
        "|---|---|---|---|---|---|---|",
    ]
    for arm, item in payload["metrics"].items():
        lines.append(
            f"| {arm} | {item['quality']:.3f} | {item['mean_completion_tokens']:.1f} "
            f"| {item['mean_prompt_tokens']:.1f} | {item['p50_latency_ms']:.0f} "
            f"| {item['cache_hits']} | {item['risk_denies']} |"
        )
    selection = payload["selection"]
    lines.extend(
        [
            "",
            "## 选择结果",
            "",
            f"- decision: **{selection['decision']}**",
            f"- chosen_arm: {selection['chosen_arm']}",
            f"- baseline: {selection['baseline_arm']}",
            f"- delta_ci: {selection['delta_ci']}",
            f"- reason: {selection['reason']}",
            "",
            "## 校准",
            "",
        ]
    )
    for key, item in payload["calibrations"].items():
        lines.append(f"- {key}: {item['result']}")
    lines.extend(
        [
            "",
            "## 环境指纹",
            "",
            "```json",
            json.dumps(payload["run"]["fingerprint"], ensure_ascii=False, indent=2),
            "```",
            "",
            "> 原始逐题数据见 `results.json`；后端失败时本报告不会生成。",
        ]
    )
    return "\n".join(lines) + "\n"


__all__ = ["ARMS", "MatrixConfig", "load_matrix_config", "run_matrix"]
