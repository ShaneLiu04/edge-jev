"""共享前缀长文数据集（AR007 FR7-3，design §3.6）。

同一份文档包含多个「编号→校验码」记录，问题只替换目标编号；
因此所有请求的文档前缀逐字一致，可验证真实 prefix/KV 复用。
"""

from __future__ import annotations

from jev.bench.dataset import Gold, QualityQuestion

_FILLER = (
    "第{index}段仓库巡检记录：区域 {zone} 的温湿度为 {temp}%/{humidity}%，"
    "抽检包裹 {count} 件，负责人巡检员{owner}，记录已归档至周报。"
)

_NEEDLE = (
    "第{index}段特别记录：编号 K-{target:04d} 的校验码为 {code}，"
    "该记录仅出现一次，若被问及必须原样返回该数字。"
)


def build_shared_prefix(count: int = 12, *, paragraphs: int = 60) -> list[QualityQuestion]:
    """生成 count 个问题；所有问题共享同一份文档前缀。"""
    if count < 1:
        raise ValueError("count 必须 >=1")
    if paragraphs < max(6, count + 4):
        raise ValueError("paragraphs 太小，无法容纳全部目标记录")
    targets = [(1000 + index, 4815 + index * 37) for index in range(count)]
    needle_positions = _spread(len(targets), paragraphs)
    lines: list[str] = []
    target_index = 0
    for paragraph in range(paragraphs):
        if target_index < len(targets) and paragraph == needle_positions[target_index]:
            target, code = targets[target_index]
            lines.append(_NEEDLE.format(index=paragraph, target=target, code=code))
            target_index += 1
            continue
        lines.append(
            _FILLER.format(
                index=paragraph,
                zone=chr(65 + paragraph % 6),
                temp=18 + paragraph % 8,
                humidity=40 + paragraph % 20,
                count=10 + paragraph * 7 % 90,
                owner=chr(65 + paragraph % 5),
            )
        )
    document = "\n\n".join(lines)
    questions: list[QualityQuestion] = []
    for index, (target, code) in enumerate(targets, start=1):
        prompt = (
            "以下是一份仓库巡检记录。\n\n"
            f"{document}\n\n"
            f"问题：编号 K-{target:04d} 的校验码是多少？只输出数字。"
        )
        questions.append(
            QualityQuestion(
                id=f"shared-{index:02d}",
                prompt=prompt,
                gold=Gold("numeric", str(code), 0.0),
                lang="zh",
                category="shared_prefix",
            )
        )
    return questions


def _spread(count: int, paragraphs: int) -> list[int]:
    """把目标段均匀放在非首末位置（确定性）。"""
    usable = paragraphs - 2
    if count > usable:
        raise ValueError("目标数超过可用段落")
    return [1 + round(index * (usable - 1) / max(1, count - 1)) for index in range(count)]


__all__ = ["build_shared_prefix"]
