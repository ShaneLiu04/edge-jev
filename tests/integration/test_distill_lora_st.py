"""ST-032（FR12 验收 3，可选路径）：kev LoRA 微调真实执行并产出适配器。

真实链路：真蒸馏数据集（TrajectorySample）→ train_lora 桥接 → 真实
kev.train 子进程（models/kev/repo 独立 venv，Python 3.12 + torch）→
适配器产物（head.pt + LoRA adapter）落盘。

环境记录：Windows 下内网 pytorch 镜像仅 Linux CUDA 轮，GPU 训练不可得，
本用例走 CPU 路径（device=cpu）——编排/数据桥/产物契约与 GPU 路径一致
（kev.train 同一代码路径，仅设备与速度不同）；HF 基座下载走内网镜像。
"""

import json
from pathlib import Path

import pytest

from jev.distill.dataset import DistillDataset, TrajectorySample
from jev.distill.lora import train_lora
from jev.kernel.decisions.builtin import load_builtin_registry

pytestmark = [pytest.mark.assets, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]
KEV_REPO = REPO_ROOT / "models" / "kev" / "repo"
REGISTRY = load_builtin_registry()

MESSAGES_STATE = json.dumps(
    [{"role": "user", "content": "写一篇综述"}, {"role": "assistant", "content": "好的"}],
    ensure_ascii=False,
)


def _sample(request_id: str, point_id: str, gold: str, probabilities: dict,
            question_type: str = "choice") -> TrajectorySample:
    return TrajectorySample(
        request_id=request_id,
        point_id=point_id,
        state=MESSAGES_STATE,
        question=f"{point_id}: 演示问题",
        question_type=question_type,
        gold=gold,
        gold_answer="大脑答案",
        value=max(probabilities, key=probabilities.__getitem__),
        confidence=0.9,
        probabilities=probabilities,
        from_fallback=False,
    )


def _dataset() -> DistillDataset:
    samples = [
        _sample("req-1", "intent", "complex",
                {"decision": 0.1, "simple": 0.1, "complex": 0.8}),
        _sample("req-1", "needs_retry", "no_retry",
                {"retry": 0.2, "no_retry": 0.8}),
        _sample("req-2", "complexity", "high",
                {"0": 0.1, "1": 0.2, "2": 0.7}, question_type="score"),
    ]
    return DistillDataset(
        samples=tuple(samples), train=tuple(samples), dev=(), test=()
    )


def test_train_lora_real_kev_train_produces_adapter(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_ENDPOINT", "http://mirrors.tools.huawei.com/huggingface")
    monkeypatch.setenv("HF_HUB_DISABLE_XET", "1")

    out = train_lora(
        _dataset(), REGISTRY,
        kev_repo=KEV_REPO,
        out_dir=tmp_path / "lora",
        device="cpu",
        epochs=1,
        lora_rank=8,
        timeout_s=3600.0,
    )

    data_path = out / "lora-data.jsonl"
    assert data_path.is_file()
    lines = data_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2  # 按 request 聚合

    adapter_dir = out / "adapter"
    # kev.train 产物契约（serve --run 可加载）：head.pt + LoRA adapter 文件
    assert (adapter_dir / "head.pt").is_file()
    assert (adapter_dir / "adapter_config.json").is_file()
    config = json.loads((adapter_dir / "adapter_config.json").read_text("utf-8"))
    assert config["r"] == 8
    metrics = adapter_dir / "training_metrics.json"
    assert metrics.is_file()
