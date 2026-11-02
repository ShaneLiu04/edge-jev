"""文档数值推理数据集（AR021）：同一文档中多记录，问题需要跨记录运算。

问题覆盖求和、绝对差、最大/最小数值、最大/最小记录编号；答案可从文档
唯一记录确定性计算，用于验证 GQRA 的接地算术快路径。
"""

from __future__ import annotations

from jev.bench.dataset import Gold, QualityQuestion

_FILLER = (
    "第{index}段仓库巡检记录：编号 K-{other:04d} 的入库数量为 {count} 件，"
    "温湿度读数 {temp}%/{humidity}%，负责人巡检员{owner}，记录已归档。"
)
_RECORD = (
    "第{index}段特别记录：编号 K-{target:04d} 的校验码是 {code}，"
    "该记录仅出现一次，若被问及必须原样使用。"
)

_TARGETS = [(1000 + index * 7, 4815 + index * 37) for index in range(16)]
_PAIRS = [(0, 1), (2, 3), (4, 5), (6, 7), (8, 9), (10, 11), (12, 13), (14, 15)]


def build_numeric_reasoning(count: int = 12, *, paragraphs: int = 60) -> list[QualityQuestion]:
    """生成 count 个跨记录数值/比较问题，文档前缀逐字一致。"""
    if count < 1:
        raise ValueError("count 必须 >=1")
    if count > len(_PAIRS) * 3:
        raise ValueError("count 超出数据集容量")
    needles = {index: target for index, target in enumerate(_TARGETS)}
    lines: list[str] = []
    for paragraph in range(paragraphs):
        record_index = _record_at(paragraph, len(_TARGETS), paragraphs)
        if record_index is not None:
            target, code = needles[record_index]
            lines.append(_RECORD.format(index=paragraph, target=target, code=code))
            continue
        other = 3000 + ((paragraph * 19 + 7) % 700)
        lines.append(
            _FILLER.format(
                index=paragraph,
                other=other,
                count=10 + (paragraph * 11) % 90,
                temp=18 + paragraph % 9,
                humidity=40 + paragraph % 21,
                owner=chr(65 + paragraph % 6),
            )
        )
    document = "\n\n".join(lines)
    questions: list[QualityQuestion] = []
    for index in range(count):
        pair = _PAIRS[index % len(_PAIRS)]
        left, right = needles[pair[0]], needles[pair[1]]
        kind = ("sum", "diff", "max", "min", "which_max", "which_min")[index % 6]
        if kind == "sum":
            prompt = (
                f"编号 K-{left[0]:04d} 和 K-{right[0]:04d} 的校验码之和是多少？"
                "只输出数字。"
            )
            gold = Gold("numeric", str(left[1] + right[1]), 0.0)
        elif kind == "diff":
            prompt = (
                f"编号 K-{left[0]:04d} 与 K-{right[0]:04d} 的校验码相差多少？"
                "只输出数字。"
            )
            gold = Gold("numeric", str(abs(left[1] - right[1])), 0.0)
        elif kind == "max":
            prompt = (
                f"编号 K-{left[0]:04d} 和 K-{right[0]:04d} 中较大的校验码是多少？"
                "只输出数字。"
            )
            gold = Gold("numeric", str(max(left[1], right[1])), 0.0)
        elif kind == "min":
            prompt = (
                f"编号 K-{left[0]:04d} 和 K-{right[0]:04d} 中较小的校验码是多少？"
                "只输出数字。"
            )
            gold = Gold("numeric", str(min(left[1], right[1])), 0.0)
        elif kind == "which_max":
            target = left if left[1] > right[1] else right
            prompt = (
                f"编号 K-{left[0]:04d} 和 K-{right[0]:04d} 中哪个校验码更大？"
                "只输出编号。"
            )
            gold = Gold("contains", f"K-{target[0]:04d}", 0.0)
        else:
            target = left if left[1] < right[1] else right
            prompt = (
                f"编号 K-{left[0]:04d} 和 K-{right[0]:04d} 中哪个校验码更小？"
                "只输出编号。"
            )
            gold = Gold("contains", f"K-{target[0]:04d}", 0.0)
        questions.append(
            QualityQuestion(
                id=f"numeric-{index + 1:02d}",
                prompt=(
                    "以下是一份仓库巡检记录。\n\n"
                    f"{document}\n\n问题：{prompt}"
                ),
                gold=gold,
                lang="zh",
                category="numeric_reasoning",
            )
        )
    return questions


def _record_at(paragraph: int, record_count: int, paragraphs: int) -> int | None:
    if record_count >= paragraphs - 2:
        return None
    positions = {
        1 + round(index * (paragraphs - 3) / max(1, record_count - 1)): index
        for index in range(record_count)
    }
    return positions.get(paragraph)


__all__ = ["build_numeric_reasoning"]
