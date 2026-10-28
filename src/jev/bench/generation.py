"""共享前缀 + 长生成数据集（AR008 投机解码联合验证）。

同一份长文档、不同目标编号；问题要求详细说明并在末行输出校验码，
使前缀可被 KV 复用、解码阶段足够长以体现投机解码收益。
"""

from __future__ import annotations

from jev.bench.dataset import Gold, QualityQuestion

_FILLER = (
    "第{index}段仓库巡检记录：区域 {zone} 温度 {temp}% 湿度 {humidity}%，"
    "抽检包裹 {count} 件，负责人巡检员{owner}，未发现异常，记录归档。"
)

_NEEDLE = (
    "第{index}段特别记录：编号 K-{target:04d} 的校验码为 {code}，"
    "该记录仅出现一次，若被问及必须原样返回该数字。"
)


def build_shared_generation(
    count: int = 8, *, paragraphs: int = 60
) -> list[QualityQuestion]:
    if count < 1:
        raise ValueError("count 必须 >=1")
    if paragraphs < max(8, count + 4):
        raise ValueError("paragraphs 太小")
    targets = [(2000 + index, 7000 + index * 41) for index in range(count)]
    positions = _spread(count, paragraphs)
    lines: list[str] = []
    cursor = 0
    for paragraph in range(paragraphs):
        if cursor < count and paragraph == positions[cursor]:
            target, code = targets[cursor]
            lines.append(_NEEDLE.format(index=paragraph, target=target, code=code))
            cursor += 1
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
            f"问题：请用至少 80 个字详细说明编号 K-{target:04d} 的记录，"
            f"并在最后一行输出：校验码={code}。"
        )
        questions.append(
            QualityQuestion(
                id=f"gen-{index:02d}",
                prompt=prompt,
                gold=Gold("contains", str(code)),
                lang="zh",
                category="shared_generation",
            )
        )
    return questions


def build_long_generation(count: int = 12) -> list[QualityQuestion]:
    """短 prompt + 长输出：隔离解码阶段，验证投机解码收益。"""
    if count < 1:
        raise ValueError("count 必须 >=1")
    questions: list[QualityQuestion] = []
    for index in range(1, count + 1):
        code = str(7000 + index * 41)
        prompt = (
            "请写一段至少 100 字的说明，解释为什么缓存能降低 Web 延迟，"
            "要求包含“命中率”和“复用”两个词，并在最后一行只输出："
            f"校验码={code}。"
        )
        questions.append(
            QualityQuestion(
                id=f"long-{index:02d}",
                prompt=prompt,
                gold=Gold("contains", code),
                lang="zh",
                category="long_generation",
            )
        )
    return questions


def build_long_generation_en(count: int = 12) -> list[QualityQuestion]:
    """English short-prompt / long-output set (speculative acceptance check)."""
    if count < 1:
        raise ValueError("count must be >=1")
    questions: list[QualityQuestion] = []
    for index in range(1, count + 1):
        prompt = (
            "Write at least 100 words explaining why caching reduces web latency. "
            "Mention hit rate and reuse."
        )
        questions.append(
            QualityQuestion(
                id=f"longen-{index:02d}",
                prompt=prompt,
                gold=Gold("regex", "(?i)(hit|reuse)"),
                lang="en",
                category="long_generation_en",
            )
        )
    return questions


def _spread(count: int, paragraphs: int) -> list[int]:
    usable = paragraphs - 2
    if count > usable:
        raise ValueError("目标数超过可用段落")
    if count == 1:
        return [1]
    return [1 + round(index * (usable - 1) / (count - 1)) for index in range(count)]


__all__ = ["build_long_generation", "build_long_generation_en", "build_shared_generation"]
