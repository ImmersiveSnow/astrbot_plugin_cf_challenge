"""范围命令解析独立出来，方便在无 AstrBot 环境中验证。"""

import json
from dataclasses import asdict

from .models import ChallengeError, ProblemRange


def parse_range(text: str, current: ProblemRange) -> ProblemRange:
    if text.lstrip().startswith("{"):
        try:
            changes = json.loads(text)
        except ValueError as exc:
            raise ChallengeError("范围 JSON 格式无效，请使用英文双引号。") from exc
        if not isinstance(changes, dict) or set(changes) - set(asdict(current)):
            raise ChallengeError(
                "范围字段只支持难度、标签和比赛编号，具体字段见 /cf 帮助。"
            )
        return ProblemRange.from_dict({**asdict(current), **changes})
    parts = text.split()
    if len(parts) != 2:
        raise ChallengeError(
            "用法：/cf 范围 1200 1800；完整条件可用 JSON，见 /cf 帮助。"
        )
    return ProblemRange.from_dict(
        {**asdict(current), "min_rating": parts[0], "max_rating": parts[1]}
    )
