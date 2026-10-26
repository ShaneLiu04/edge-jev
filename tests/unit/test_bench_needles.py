"""AR005 T011：needles 长上下文数据集（确定性、可判分）。"""

from jev.bench.dataset import grade
from jev.bench.needles import build_needles


def test_needles_are_deterministic_and_gradeable():
    first = build_needles(5)
    second = build_needles(5)
    assert [q.prompt for q in first] == [q.prompt for q in second]
    assert len(first) == 5
    for index, question in enumerate(first, start=1):
        assert question.id == f"needle-{index:02d}"
        assert question.category == "long_context"
        assert len(question.prompt) > 2000
        target = 1000 + (index - 1) * 7
        code = 4815 + (index - 1) * 37
        assert f"K-{target:04d}" in question.prompt
        assert str(code) in question.prompt
        assert grade(str(code), question.gold) is True
        assert grade("0", question.gold) is False


def test_needle_not_in_first_or_last_paragraph():
    question = build_needles(1)[0]
    paragraphs = [part for part in question.prompt.split("\n\n") if part]
    target_line = next(
        part for part in paragraphs if "特别记录" in part
    )
    assert target_line not in (paragraphs[0], paragraphs[-1])


def test_needles_input_validation():
    import pytest

    with pytest.raises(ValueError):
        build_needles(0)
    with pytest.raises(ValueError):
        build_needles(1, paragraphs=3)
