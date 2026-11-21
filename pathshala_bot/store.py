"""Run history: what was posted, topics/headlines already covered, generated content.

Backed by Supabase when SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY are set (recommended for
servers: survives redeploys), otherwise a local SQLite file (DB_PATH) for development.
Use `get_history()` rather than constructing a backend directly.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
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
    publish TEXT,
    content TEXT
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteHistory:
    backend = "sqlite"

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with closing(self._conn()) as c:
            c.executescript(SCHEMA)
            cols = {r[1] for r in c.execute("PRAGMA table_info(runs)")}
            if "content" not in cols:  # databases created before content was stored
                c.execute("ALTER TABLE runs ADD COLUMN content TEXT")

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def recent_topics(self, exam_id: str, limit: int = 40) -> list[str]:
        with closing(self._conn()) as c:
            rows = c.execute(
                "SELECT topic FROM runs WHERE exam_id=? AND topic IS NOT NULL AND topic != '' ORDER BY id DESC LIMIT ?",
                (exam_id, limit),
            ).fetchall()
        return [r[0] for r in rows]

    def record(self, exam_id: str, subject: str, topic: str, content_type: str, run_dir: str, status: str,
               publish: dict, content: dict | None = None) -> None:
        with closing(self._conn()) as c, c:
            c.execute(
                "INSERT INTO runs(created_at, exam_id, subject, topic, content_type, run_dir, status, publish, content) VALUES (?,?,?,?,?,?,?,?,?)",
                (_now(), exam_id, subject, topic, content_type, run_dir, status, json.dumps(publish, ensure_ascii=False),
                 json.dumps(content, ensure_ascii=False) if content is not None else None),
            )

    def recent_headlines(self, exam_id: str, days: int = 7) -> list[str]:
        since = (date.today() - timedelta(days=days)).isoformat()
        with closing(self._conn()) as c:
            rows = c.execute("SELECT headline FROM headlines WHERE exam_id=? AND day >= ? ORDER BY id DESC", (exam_id, since)).fetchall()
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
            rows = c.execute(
                "SELECT id, created_at, exam_id, subject, topic, content_type, run_dir, status, publish FROM runs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def last_runs(self, limit: int = 10) -> list[tuple]:
        with closing(self._conn()) as c:
            return c.execute(
                "SELECT created_at, exam_id, topic, content_type, status FROM runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()

    def recent_contents(self, exam_id: str | None = None, limit: int = 50) -> list[dict]:
        """Generated content packages (newest first), for /quiz."""
        q = "SELECT content FROM runs WHERE content IS NOT NULL AND content_type != 'akhbar'"
        args: tuple = ()
        if exam_id:
            q += " AND exam_id=?"
            args = (exam_id,)
        with closing(self._conn()) as c:
            rows = c.execute(q + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
        return [json.loads(r[0]) for r in rows]


class SupabaseHistory:
    backend = "supabase"

    def __init__(self, db=None):
        from .supabase_db import Supabase

        self.db = db or Supabase()

    def recent_topics(self, exam_id: str, limit: int = 40) -> list[str]:
        rows = self.db.select("runs", {"select": "topic", "exam_id": f"eq.{exam_id}", "topic": "neq.",
                                       "order": "id.desc", "limit": str(limit)})
        return [r["topic"] for r in rows if r.get("topic")]

    def record(self, exam_id: str, subject: str, topic: str, content_type: str, run_dir: str, status: str,
               publish: dict, content: dict | None = None) -> None:
        self.db.insert("runs", {"exam_id": exam_id, "subject": subject, "topic": topic, "content_type": content_type,
                                "run_dir": run_dir, "status": status, "publish": publish, "content": content})

    def recent_headlines(self, exam_id: str, days: int = 7) -> list[str]:
        since = (date.today() - timedelta(days=days)).isoformat()
        rows = self.db.select("headlines", {"select": "headline", "exam_id": f"eq.{exam_id}", "day": f"gte.{since}", "order": "id.desc"})
        return [r["headline"] for r in rows]

    def add_headlines(self, exam_id: str, day: str, headlines: list[str]) -> None:
        if headlines:
            self.db.insert("headlines", [{"exam_id": exam_id, "day": day, "headline": h} for h in headlines])

    def akhbar_count(self, exam_id: str) -> int:
        return self.db.count("runs", {"exam_id": f"eq.{exam_id}", "content_type": "eq.akhbar", "status": "neq.failed"})

    def runs(self, limit: int = 50) -> list[dict]:
        rows = self.db.select("runs", {"select": "id,created_at,exam_id,subject,topic,content_type,run_dir,status,publish",
                                       "order": "id.desc", "limit": str(limit)})
        for r in rows:
            r["publish"] = json.dumps(r.get("publish") or {}, ensure_ascii=False)  # same shape as SQLite
        return rows

    def last_runs(self, limit: int = 10) -> list[tuple]:
        return [(r["created_at"], r["exam_id"], r["topic"], r["content_type"], r["status"]) for r in self.runs(limit)]

    def recent_contents(self, exam_id: str | None = None, limit: int = 50) -> list[dict]:
        params = {"select": "content", "content": "not.is.null", "content_type": "neq.akhbar", "order": "id.desc", "limit": str(limit)}
        if exam_id:
            params["exam_id"] = f"eq.{exam_id}"
        return [r["content"] for r in self.db.select("runs", params) if r.get("content")]


# Backwards compatible name
History = SQLiteHistory


def get_history():
    """Supabase if configured in .env, else the local SQLite file."""
    from .config import settings
    from .supabase_db import configured

    if configured():
        return SupabaseHistory()
    return SQLiteHistory(settings.db_path)
