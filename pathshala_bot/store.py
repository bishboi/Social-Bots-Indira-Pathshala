"""SQLite history so the bot doesn't repeat topics."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    exam_id TEXT NOT NULL,
    subject TEXT,
    topic TEXT,
    content_type TEXT,
    run_dir TEXT,
    status TEXT,
    publish TEXT
);
CREATE INDEX IF NOT EXISTS runs_exam ON runs(exam_id, created_at);
CREATE TABLE IF NOT EXISTS headlines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exam_id TEXT NOT NULL,
    day TEXT NOT NULL,
    headline TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS headlines_exam ON headlines(exam_id, day);
"""


class History:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with closing(self._conn()) as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def recent_topics(self, exam_id: str, limit: int = 40) -> list[str]:
        with closing(self._conn()) as c:
            rows = c.execute(
                "SELECT topic FROM runs WHERE exam_id=? AND topic IS NOT NULL AND topic != '' ORDER BY id DESC LIMIT ?",
                (exam_id, limit),
            ).fetchall()
        return [r[0] for r in rows]

    def record(self, exam_id: str, subject: str, topic: str, content_type: str, run_dir: str, status: str, publish: dict) -> None:
        with closing(self._conn()) as c, c:
            c.execute(
                "INSERT INTO runs(created_at, exam_id, subject, topic, content_type, run_dir, status, publish) VALUES (?,?,?,?,?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(), exam_id, subject, topic, content_type, run_dir, status, json.dumps(publish, ensure_ascii=False)),
            )

    def recent_headlines(self, exam_id: str, days: int = 7) -> list[str]:
        with closing(self._conn()) as c:
            rows = c.execute(
                "SELECT headline FROM headlines WHERE exam_id=? AND day >= date('now', ?) ORDER BY id DESC",
                (exam_id, f"-{days} day"),
            ).fetchall()
        return [r[0] for r in rows]

    def add_headlines(self, exam_id: str, day: str, headlines: list[str]) -> None:
        with closing(self._conn()) as c, c:
            c.executemany("INSERT INTO headlines(exam_id, day, headline) VALUES (?,?,?)", [(exam_id, day, h) for h in headlines])

    def akhbar_count(self, exam_id: str) -> int:
        with closing(self._conn()) as c:
            return c.execute("SELECT COUNT(*) FROM runs WHERE exam_id=? AND content_type='akhbar' AND status!='failed'", (exam_id,)).fetchone()[0]

    def runs(self, limit: int = 50) -> list[dict]:
        with closing(self._conn()) as c:
            c.row_factory = sqlite3.Row
            rows = c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def last_runs(self, limit: int = 10) -> list[tuple]:
        with closing(self._conn()) as c:
            return c.execute(
                "SELECT created_at, exam_id, topic, content_type, status FROM runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
