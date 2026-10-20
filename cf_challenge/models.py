"""与 AstrBot 无关的数据和选题规则。"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any


class ChallengeError(Exception):
    """可以直接发给群友的错误，不包含未揭晓的题号或内部服务细节。"""


@dataclass(frozen=True)
class Problem:
    contest_id: int
    index: str
    name: str
    rating: int
    tags: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return f"{self.contest_id}{self.index}"

    @property
    def url(self) -> str:
        return (
            f"https://codeforces.com/problemset/problem/{self.contest_id}/{self.index}"
        )

    @classmethod
    def from_api(cls, value: dict[str, Any]) -> Problem | None:
        contest = value.get("contestId")
        rating = value.get("rating")
        index = value.get("index", "")
        if (
            type(contest) is not int
            or contest <= 0
            or type(rating) is not int
            or not isinstance(index, str)
            or not re.fullmatch(r"[A-Z][A-Z0-9]{0,5}", index)
            or value.get("type") != "PROGRAMMING"
        ):
            return None
        return cls(contest, index, str(value["name"]), rating, tuple(value["tags"]))

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Problem:
        return cls(**{**value, "tags": tuple(value.get("tags", []))})


@dataclass(frozen=True)
class ProblemRange:
    min_rating: int = 800
    max_rating: int = 1600
    tags: tuple[str, ...] = ()
    exclude_tags: tuple[str, ...] = ()
    min_contest: int = 1
    max_contest: int = 99999

    def __post_init__(self) -> None:
        if not 0 <= self.min_rating <= self.max_rating <= 10000:
            raise ChallengeError("难度范围无效，请填写 0～10000 内从小到大的两个数。")
        if not 1 <= self.min_contest <= self.max_contest <= 99999:
            raise ChallengeError(
                "比赛编号范围无效，请填写 1～99999 内从小到大的两个数。"
            )
        if set(self.tags) & set(self.exclude_tags):
            raise ChallengeError("同一标签不能同时出现在必选和排除列表中。")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ProblemRange:
        for key in ("tags", "exclude_tags"):
            if key in value and (
                not isinstance(value[key], (list, tuple))
                or not all(isinstance(tag, str) for tag in value[key])
            ):
                raise ChallengeError('标签必须填写字符串列表，例如 ["dp", "graphs"]。')
        try:
            return cls(
                min_rating=int(value.get("min_rating", 800)),
                max_rating=int(value.get("max_rating", 1600)),
                tags=tuple(
                    str(t).strip() for t in value.get("tags", []) if str(t).strip()
                ),
                exclude_tags=tuple(
                    str(t).strip()
                    for t in value.get("exclude_tags", [])
                    if str(t).strip()
                ),
                min_contest=int(value.get("min_contest", 1)),
                max_contest=int(value.get("max_contest", 99999)),
            )
        except (ValueError, TypeError) as exc:
            raise ChallengeError("范围配置格式不正确，请检查整数和标签列表。") from exc

    def accepts(self, problem: Problem) -> bool:
        return (
            self.min_rating <= problem.rating <= self.max_rating
            and self.min_contest <= problem.contest_id <= self.max_contest
            and set(self.tags).issubset(problem.tags)
            and not set(self.exclude_tags).intersection(problem.tags)
        )

    def describe(self) -> str:
        tags = "、".join(self.tags) or "不限"
        excluded = "、".join(self.exclude_tags) or "无"
        return (
            f"难度：{self.min_rating}～{self.max_rating}\n"
            f"必含标签（全部满足）：{tags}\n排除标签：{excluded}\n"
            f"比赛编号：{self.min_contest}～{self.max_contest}"
        )


@dataclass(frozen=True)
class Statement:
    body: str
    input_spec: str
    output_spec: str
    samples: tuple[tuple[str, str], ...]
    time_limit: str
    memory_limit: str
    note: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Statement:
        try:
            fields = ("body", "input_spec", "output_spec", "time_limit", "memory_limit")
            if any(
                not isinstance(value.get(k), str) or not value[k].strip()
                for k in fields
            ):
                raise ValueError("missing statement field")
            samples = value.get("samples")
            if not isinstance(samples, list) or not samples:
                raise ValueError("missing samples")
            pairs = []
            for pair in samples:
                if (
                    not isinstance(pair, (list, tuple))
                    or len(pair) != 2
                    or not all(isinstance(s, str) and s.strip() for s in pair)
                ):
                    raise ValueError("invalid sample")
                pairs.append(tuple(pair))
            note = value.get("note", "")
            if not isinstance(note, str):
                raise ValueError("invalid note")
            return cls(**{k: value[k] for k in fields}, samples=tuple(pairs), note=note)
        except (KeyError, TypeError, ValueError) as exc:
            raise ChallengeError(
                "题面数据不完整，需要正文、输入输出、限制和成对样例。"
            ) from exc


@dataclass(frozen=True)
class Prepared:
    body: str
    input_spec: str
    output_spec: str
    note: str
    reference: str


@dataclass(frozen=True)
class Review:
    verdict: str
    feedback: str
    proof: str
    complexity: str
    counterexample: str = ""


@dataclass
class Round:
    id: str
    scope: str
    problem: Problem
    source: Statement
    prepared: Prepared
    status: str = "publishing"
    solver: str = ""
    created_at: float = 0.0
    attempts: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Round:
        return cls(
            **{
                **value,
                "problem": Problem.from_dict(value["problem"]),
                "source": Statement.from_dict(value["source"]),
                "prepared": Prepared(**value["prepared"]),
            }
        )
