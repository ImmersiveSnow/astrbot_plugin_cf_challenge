"""只从允许公开的字段构造题面；参考分析永远不进入群消息。"""

import re
from dataclasses import dataclass

from .models import Problem, Review, Round


def hide_identity(text: str, problem: Problem) -> str:
    text = re.sub(r"https?://\S+", "[链接已隐藏]", text, flags=re.I)
    text = re.sub(
        rf"(?<![A-Za-z0-9])(?:CF\s*)?{re.escape(str(problem.contest_id))}"
        rf"\s*{re.escape(problem.index)}(?![A-Za-z0-9])",
        "[题号已隐藏]",
        text,
        flags=re.I,
    )
    # 避免短题名（如 A）误伤变量。
    if len(problem.name) >= 4:
        text = re.sub(re.escape(problem.name), "[原题名已隐藏]", text, flags=re.I)
    return text


def chunks(text: str, limit: int = 1500) -> list[str]:
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit + 1)
        if cut < limit // 2:
            cut = limit
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        parts.append(text)
    return parts


@dataclass(frozen=True, slots=True)
class StatementSection:
    title: str
    text: str
    preformatted: bool = False


def statement_sections(round_: Round) -> list[StatementSection]:
    """图片与文字共用同一组公开字段；样例不做身份替换或公式处理。"""
    source, translated = round_.source, round_.prepared
    sections = [
        StatementSection(
            f"算法挑战 #{round_.id[:8]}",
            f"时间限制：{source.time_limit}\n内存限制：{source.memory_limit}",
        ),
        StatementSection("题目描述", translated.body),
        StatementSection("输入格式", translated.input_spec),
        StatementSection("输出格式", translated.output_spec),
    ]
    if translated.note:
        sections.append(StatementSection("说明", translated.note))
    result = [
        StatementSection(
            hide_identity(section.title, round_.problem),
            hide_identity(section.text, round_.problem),
        )
        for section in sections
    ]
    for i, (sample_in, sample_out) in enumerate(source.samples, start=1):
        result.append(
            StatementSection(
                f"样例 {i}",
                f"输入：\n{sample_in}\n\n输出：\n{sample_out}",
                preformatted=True,
            )
        )
    return result


def statement_nodes(round_: Round) -> list[str]:
    return [
        part
        for section in statement_sections(round_)
        for part in chunks(f"{section.title}\n{section.text}")
    ]


def reveal_text(round_: Round) -> str:
    p = round_.problem
    return f"原题：CF {p.key} · {p.name}\n难度：{p.rating}\n{p.url}"


def review_text(review: Review, round_: Round) -> str:
    if review.verdict == "accepted":
        p = round_.problem
        return (
            f"拿下本题 first blood！本题是 CF {p.key} · {p.name} ({p.rating}) {p.url}"
        )
    return hide_identity(review.feedback, round_.problem)
