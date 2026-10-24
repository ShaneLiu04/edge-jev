"""kev-0.8B LoRA 微调路径（design.md 流程图 D 可选分支，FR12，T020）。

桥接 jev 蒸馏数据集 → kev 训练器（`kev.train --data`，用户自有 JSONL
路径——记录为 TypeSafe 请求形状 + 每问 `label`：choice=选项名 /
score=等级索引）：

- 训练在 kev 仓库独立环境执行（kev 需 Python ≥3.12 + torch 栈，经
  `uv run --project` 隔离；本包 venv 3.11 不受影响）。
- 适配器经 `kev.serve --run <out>` 提供服务，T004 KevHttpBackend 以
  serve_url 接入（[cerebellum] default_backend="kev" 配置即可，无需
  改后端代码）。
"""

import json
import logging
import subprocess
from pathlib import Path

from jev.distill.dataset import DistillDataset, TrajectorySample
from jev.errors import JevAssetError, JevError
from jev.kernel.decisions.framework import DecisionRegistry

logger = logging.getLogger(__name__)


def to_kev_records(dataset: DistillDataset, registry: DecisionRegistry) -> list[dict]:
    """样本（按 request_id 聚合）→ kev load_records 兼容记录。

    state 取 messages JSON 展平为纯文本（逐消息 content 换行连接）；
    每决策点一问：instructions=点问题文本，choice criteria=选项名→None，
    score criteria=等级列表；label=gold（choice 选项名 / score 等级索引）。
    """
    by_request: dict[str, dict[str, TrajectorySample]] = {}
    known_ids = set(registry.ids)
    for sample in dataset.samples:
        if sample.point_id not in known_ids or sample.question_type not in ("choice", "score"):
            continue
        by_request.setdefault(sample.request_id, {})[sample.point_id] = sample

    records: list[dict] = []
    for request_id in sorted(by_request):
        points = by_request[request_id]
        first = next(iter(points.values()))
        questions: dict[str, dict] = {}
        for point_id in sorted(points):
            sample = points[point_id]
            point = registry.get(point_id)
            if sample.question_type == "choice":
                options = list(sample.probabilities) or list(point.options)
                if sample.gold not in options:
                    continue  # gold 不在选项域（理论不可达，保守跳过）
                questions[point_id] = {
                    "type": "choice",
                    "instructions": sample.question,
                    "criteria": {name: None for name in options},
                    "label": sample.gold,
                }
            else:
                levels = list(point.options)
                if sample.gold not in levels:
                    continue
                questions[point_id] = {
                    "type": "score",
                    "instructions": sample.question,
                    "criteria": levels,
                    "label": levels.index(sample.gold),
                }
        if questions:
            records.append(
                {"state": _flatten_state(first.state), "questions": questions}
            )
    return records


def _flatten_state(state: str) -> str:
    """messages JSON → 纯文本 state（解析失败原样返回）。"""
    try:
        messages = json.loads(state)
    except (ValueError, TypeError):
        return state
    if not isinstance(messages, list):
        return state
    parts = [
        str(message.get("content", ""))
        for message in messages
        if isinstance(message, dict) and message.get("content")
    ]
    return "\n".join(parts) if parts else state


def write_training_jsonl(records: list[dict], path: Path | str) -> Path:
    """训练 JSONL 落盘（kev.train --data 输入格式）。"""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return out


def build_train_command(
    kev_repo: Path | str,
    data_path: Path | str,
    out_dir: Path | str,
    *,
    device: str = "auto",
    epochs: int = 1,
    lora_rank: int = 16,
) -> list[str]:
    """构造 kev.train 调用命令（uv 隔离环境，Python ≥3.12 + torch 栈）。"""
    repo = Path(kev_repo)
    if not (repo / "kev" / "train.py").is_file():
        raise JevAssetError(f"kev 仓库不完整（缺 kev/train.py）: {repo}")
    return [
        "uv", "run", "--project", str(repo),
        "python", "-m", "kev.train",
        "--data", str(Path(data_path)),
        "--out", str(Path(out_dir)),
        "--device", device,
        "--epochs", str(epochs),
        "--lora", str(lora_rank),
    ]


def run_training(
    command: list[str], *, cwd: Path | str, timeout_s: float = 7200.0
) -> None:
    """真实子进程执行训练；非零退出/超时 → JevError（携带诊断尾部输出）。"""
    logger.info("kev LoRA 训练启动: %s", " ".join(command))
    try:
        result = subprocess.run(
            command, cwd=str(cwd), capture_output=True, text=True,
            timeout=timeout_s, encoding="utf-8", errors="replace",
        )
    except FileNotFoundError as exc:
        raise JevError(f"训练命令不可执行（uv 不在 PATH?）: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise JevError(f"训练超时（>{timeout_s}s）: {' '.join(command)}") from exc
    if result.returncode != 0:
        tail = "\n".join((result.stdout or "").splitlines()[-10:])
        err = "\n".join((result.stderr or "").splitlines()[-10:])
        raise JevError(
            f"kev 训练失败（exit {result.returncode}）:\n{tail}\n{err}"
        )
    logger.info("kev LoRA 训练完成: %s", " ".join(command[:6]))


def train_lora(
    dataset: DistillDataset,
    registry: DecisionRegistry,
    kev_repo: Path | str,
    out_dir: Path | str,
    *,
    device: str = "auto",
    epochs: int = 1,
    lora_rank: int = 16,
    timeout_s: float = 7200.0,
) -> Path:
    """组合入口：数据集 → 训练 JSONL → kev.train（适配器产物落 out_dir）。"""
    records = to_kev_records(dataset, registry)
    if not records:
        raise JevError("蒸馏数据集无可转换样本（choice/score 决策点）")
    out = Path(out_dir)
    data_path = write_training_jsonl(records, out / "lora-data.jsonl")
    command = build_train_command(
        kev_repo, data_path, out / "adapter",
        device=device, epochs=epochs, lora_rank=lora_rank,
    )
    run_training(command, cwd=kev_repo, timeout_s=timeout_s)
    return out


__all__ = [
    "to_kev_records",
    "write_training_jsonl",
    "build_train_command",
    "run_training",
    "train_lora",
]
