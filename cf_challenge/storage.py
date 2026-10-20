"""每群一轮；SQLite 的写入在事务中完成。"""

import json
import sqlite3
from pathlib import Path

from .models import ProblemRange, Round


class Store:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(directory / "challenges.sqlite3")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS rounds (
                id TEXT PRIMARY KEY, scope TEXT NOT NULL,
                problem_key TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS round_scope ON rounds(scope, status);
            CREATE TABLE IF NOT EXISTS ranges (
                scope TEXT PRIMARY KEY, data TEXT NOT NULL
            );
            """
        )
        # 上次退出时发送是否成功无法确定；不把未确认送达的题作为活动题。
        with self.db:
            self.db.execute(
                "UPDATE rounds SET status = 'interrupted' WHERE status = 'publishing'"
            )

    def save(self, round_: Round) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO rounds VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "status=excluded.status, data=excluded.data",
                (
                    round_.id,
                    round_.scope,
                    round_.problem.key,
                    round_.status,
                    json.dumps(round_.to_dict(), ensure_ascii=False),
                ),
            )

    def active(self, scope: str) -> Round | None:
        row = self.db.execute(
            "SELECT data FROM rounds WHERE scope=? AND status='active' "
            "ORDER BY rowid DESC LIMIT 1",
            (scope,),
        ).fetchone()
        return Round.from_dict(json.loads(row[0])) if row else None

    def seen(self, scope: str) -> set[str]:
        return {
            row[0]
            for row in self.db.execute(
                "SELECT problem_key FROM rounds WHERE scope=? "
                "AND status IN ('active', 'solved', 'revealed')",
                (scope,),
            )
        }

    def get_range(self, scope: str, default: ProblemRange) -> ProblemRange:
        row = self.db.execute(
            "SELECT data FROM ranges WHERE scope=?", (scope,)
        ).fetchone()
        return ProblemRange.from_dict(json.loads(row[0])) if row else default

    def set_range(self, scope: str, selection: ProblemRange) -> None:
        from dataclasses import asdict

        with self.db:
            self.db.execute(
                "INSERT INTO ranges VALUES (?, ?) ON CONFLICT(scope) "
                "DO UPDATE SET data=excluded.data",
                (scope, json.dumps(asdict(selection), ensure_ascii=False)),
            )

    def close(self) -> None:
        self.db.close()
