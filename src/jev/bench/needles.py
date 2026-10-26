"""长上下文 needles 数据集（AR005 JCBA/prefill 真实验证）。

确定性合成：每篇文档含多条「编号 → 校验码」干扰项，仅一个目标编号与问题匹配；
目标段落不放在首/末块，避免强制保留带来的虚假通过。答案可客观判分。
文档长度约 750–1000 token（4 字符≈1 token 估算），超过 prefill/JCBA 的 512 阈值。
"""

from __future__ import annotations

from jev.bench.dataset import Gold, QualityQuestion

_FILLER = (
    "第{index}段仓库巡检记录：编号 K-{other:04d} 的入库时间为 2026 年 {month} 月 {day} 日，"
    "负责人为巡检员{owner}，抽检包裹 {count} 件，温湿度读数 {temp}%/{humidity}%，"
    "未发现异常，记录已归档至周报。"
)

_NEEDLE = (
    "第{index}段特别记录：编号 K-{target:04d} 的校验码是 {code}，该记录仅出现一次，"
    "若被问及必须原样返回该数字。"
)


def build_needles(count: int = 20, *, paragraphs: int = 25) -> list[QualityQuestion]:
    """生成 count 篇长文问答；目标段落位于中间位置。"""
    if count < 1:
        raise ValueError("count 必须 ≥1")
    if paragraphs < 6:
        raise ValueError("paragraphs 必须 ≥6")
    questions: list[QualityQuestion] = []
    for index in range(count):
        target = 1000 + index * 7
        code = 4815 + index * 37
        target_paragraph = 1 + (index % max(1, paragraphs - 2))
        lines: list[str] = []
        for paragraph in range(paragraphs):
            if paragraph == target_paragraph:
                lines.append(_NEEDLE.format(index=paragraph, target=target, code=code))
                continue
            other = 1000 + ((index * 11 + paragraph * 13) % 900)
            lines.append(
                _FILLER.format(
                    index=paragraph,
                    other=other,
                    month=1 + (paragraph % 12),
                    day=1 + ((index + paragraph) % 28),
                    owner=chr(65 + ((index + paragraph) % 6)),
                    count=10 + (index * 3 + paragraph * 5) % 90,
                    temp=18 + (paragraph % 8),
                    humidity=40 + (paragraph % 20),
                )
            )
        document = "\n\n".join(lines)
        prompt = (
            "以下是一份仓库巡检记录。\n\n"
            f"{document}\n\n"
            f"问题：编号 K-{target:04d} 的校验码是多少？只输出数字。"
        )
        questions.append(
            QualityQuestion(
                id=f"needle-{index + 1:02d}",
                prompt=prompt,
                gold=Gold("numeric", str(code), 0.0),
                lang="zh",
                category="long_context",
            )
        )
    return questions


__all__ = ["build_needles"]
