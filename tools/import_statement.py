"""将浏览器保存的 CF HTML 或完整题面 JSON 导入插件数据目录。"""

import argparse
import json
import re
from dataclasses import asdict
from pathlib import Path

from cf_challenge.codeforces import parse_statement
from cf_challenge.models import ChallengeError, Statement


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--problem", required=True, help="CF 题号，如 4A")
    parser.add_argument("--source", required=True, type=Path, help="HTML 或 JSON 文件")
    parser.add_argument("--data-dir", required=True, type=Path, help="插件数据目录")
    parser.add_argument("--overwrite", action="store_true", help="明确允许覆盖同名题面")
    args = parser.parse_args()
    if not re.fullmatch(r"[1-9][0-9]{0,4}[A-Z][A-Z0-9]{0,5}", args.problem):
        parser.error("题号格式应类似 4A 或 1500B1")
    text = args.source.read_text(encoding="utf-8-sig")
    try:
        statement = (
            Statement.from_dict(json.loads(text))
            if args.source.suffix.lower() == ".json"
            else parse_statement(text)
        )
    except (ValueError, ChallengeError) as exc:
        parser.error(str(exc))
    target = args.data_dir / "statements" / f"{args.problem}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w" if args.overwrite else "x", encoding="utf-8") as file:
        json.dump(asdict(statement), file, ensure_ascii=False, indent=2)
    print(f"已导入：{target.resolve()}")


if __name__ == "__main__":
    main()
