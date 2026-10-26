"""AR005 T007/T009：数据集判分与方案选择/回退（FR5-4）。"""

import pytest

from jev.bench.dataset import Gold, grade, load_cache_set, load_quality_set
from jev.bench.selector import ArmOutcome, select
from jev.errors import JevProtocolError


def test_quality_set_loads_and_grades():
    questions = load_quality_set("configs/quality-set.yaml")
    assert len(questions) >= 30
    by_id = {question.id: question for question in questions}
    assert grade("答案是 8", by_id["q-001"].gold) in (True, False)
    numeric = Gold("numeric", "4", 0.0)
    assert grade("4", numeric) is True
    assert grade("结果是 4。", numeric) is True
    assert grade("5", numeric) is False
    assert grade("ACCEPT", Gold("contains", "accept")) is True
    assert grade("拒绝", Gold("contains", "accept")) is False
    assert grade("1234", Gold("regex", r"^\d{4}$")) is True
    assert grade("12a4", Gold("regex", r"^\d{4}$")) is False


def test_quality_set_rejects_bad_schema(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("questions: []\n", encoding="utf-8")
    with pytest.raises(JevProtocolError):
        load_quality_set(bad)
    bad.write_text("questions:\n  - id: a\n    prompt: p\n", encoding="utf-8")
    with pytest.raises(JevProtocolError):
        load_quality_set(bad)


def test_cache_set_loads():
    data = load_cache_set("configs/cache-set.yaml")
    assert len(data.originals) >= 10
    assert len(data.paraphrases) >= 10
    assert len(data.negatives) >= 10
    assert data.paraphrases[0].of == data.originals[0].id


def _outcomes():
    rows = []
    for index in range(24):
        qid = f"q{index}"
        band = "confident" if index % 2 == 0 else "borderline"
        rows.append(ArmOutcome("B2", qid, 1.0, 100, 100.0, band))
        rows.append(ArmOutcome("AR005", qid, 1.0, 40, 60.0, band))
    return rows


def test_select_adopts_cheaper_equal_quality():
    result = select(_outcomes(), baseline_arm="B2", min_samples=20)
    assert result.decision == "adopt"
    assert result.chosen_arm == "AR005"
    assert result.delta_ci is not None and result.delta_ci[0] > 0


def test_select_rolls_back_when_quality_drops():
    rows = []
    for index in range(24):
        qid = f"q{index}"
        rows.append(ArmOutcome("B2", qid, 1.0, 100, 100.0, "confident"))
        rows.append(ArmOutcome("AR005", qid, 0.5, 40, 60.0, "confident"))
    result = select(rows, baseline_arm="B2", min_samples=20)
    assert result.decision == "rollback"
    assert result.chosen_arm == "B2"


def test_select_rolls_back_on_small_samples():
    rows = _outcomes()[:10]
    result = select(rows, baseline_arm="B2", min_samples=20)
    assert result.decision == "rollback"
    assert "样本不足" in result.reason


def test_select_rolls_back_when_no_gain():
    rows = []
    for index in range(24):
        qid = f"q{index}"
        rows.append(ArmOutcome("B2", qid, 1.0, 100, 100.0, "confident"))
        rows.append(ArmOutcome("AR005", qid, 1.0, 100, 100.0, "confident"))
    result = select(rows, baseline_arm="B2", min_samples=20)
    assert result.decision == "rollback"


def test_select_is_deterministic():
    first = select(_outcomes(), baseline_arm="B2")
    second = select(_outcomes(), baseline_arm="B2")
    assert first == second
