"""Run history: what was posted, topics/headlines already covered, generated content.

Backed by Supabase when SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY are set (recommended for
servers: survives redeploys), otherwise a local SQLite file (DB_PATH) for development.
Use `get_history()` rather than constructing a backend directly.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

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
    content TEXT,
    space_id TEXT
);
CREATE INDEX IF NOT EXISTS runs_exam ON runs(exam_id, created_at);
CREATE TABLE IF NOT EXISTS headlines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exam_id TEXT NOT NULL,
    day TEXT NOT NULL,
    headline TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS headlines_exam ON headlines(exam_id, day);
CREATE TABLE IF NOT EXISTS test_leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    series_id TEXT NOT NULL,
    test_id TEXT NOT NULL,
    name TEXT,
    phone TEXT,
    email TEXT
);
CREATE INDEX IF NOT EXISTS test_leads_series ON test_leads(series_id, id);
CREATE TABLE IF NOT EXISTS test_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    series_id TEXT NOT NULL,
    test_id TEXT NOT NULL,
    lead_id INTEGER,
    name TEXT,
    score REAL,
    max_score REAL,
    correct INTEGER,
    wrong INTEGER,
    skipped INTEGER,
    seconds INTEGER,
    answers TEXT
);
CREATE INDEX IF NOT EXISTS test_attempts_test ON test_attempts(series_id, test_id);
"""

ATTEMPT_COLS = ("series_id", "test_id", "lead_id", "name", "score", "max_score", "correct", "wrong", "skipped", "seconds")


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
            if "space_id" not in cols:  # databases created before spaces
                c.execute("ALTER TABLE runs ADD COLUMN space_id TEXT")
            c.execute("CREATE INDEX IF NOT EXISTS runs_space ON runs(space_id, id)")

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
               publish: dict, content: dict | None = None, space_id: str | None = None) -> None:
        with closing(self._conn()) as c, c:
            c.execute(
                "INSERT INTO runs(created_at, exam_id, subject, topic, content_type, run_dir, status, publish, content, space_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (_now(), exam_id, subject, topic, content_type, run_dir, status, json.dumps(publish, ensure_ascii=False),
                 json.dumps(content, ensure_ascii=False) if content is not None else None, space_id),
            )

    def space_topics(self, space_id: str, exam_ids: list[str], limit: int = 40) -> list[dict]:
        """Recent topics of a space, newest first: {created_at, exam_id, subject, topic, content_type}.

        Also counts runs of the space's exams made before spaces existed (space_id empty)."""
        marks = ",".join("?" * len(exam_ids)) or "''"
        with closing(self._conn()) as c:
            c.row_factory = sqlite3.Row
            rows = c.execute(
                f"SELECT created_at, exam_id, subject, topic, content_type FROM runs WHERE topic IS NOT NULL AND topic != '' "
                f"AND content_type != 'akhbar' AND (space_id=? OR (space_id IS NULL AND exam_id IN ({marks}))) ORDER BY id DESC LIMIT ?",
                (space_id, *exam_ids, limit),
            ).fetchall()
        return [dict(r) for r in rows]

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
                "SELECT id, created_at, exam_id, space_id, subject, topic, content_type, run_dir, status, publish FROM runs ORDER BY id DESC LIMIT ?",
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

    # ---- test series: leads (sign-ups) and attempts
    def add_lead(self, series_id: str, test_id: str, name: str, phone: str, email: str = "") -> int:
        with closing(self._conn()) as c, c:
            cur = c.execute("INSERT INTO test_leads(created_at, series_id, test_id, name, phone, email) VALUES (?,?,?,?,?,?)",
                            (_now(), series_id, test_id, name, phone, email))
            return int(cur.lastrowid)

    def add_attempt(self, row: dict) -> None:
        with closing(self._conn()) as c, c:
            c.execute(f"INSERT INTO test_attempts(created_at, {', '.join(ATTEMPT_COLS)}, answers) VALUES (?{',?' * len(ATTEMPT_COLS)},?)",
                      (_now(), *[row.get(k) for k in ATTEMPT_COLS], json.dumps(row.get("answers") or {})))

    def test_scores(self, series_id: str, test_id: str) -> list[float]:
        with closing(self._conn()) as c:
            return [r[0] for r in c.execute("SELECT score FROM test_attempts WHERE series_id=? AND test_id=?", (series_id, test_id))]

    def series_stats(self, series_id: str) -> dict:
        with closing(self._conn()) as c:
            tests = {r[0]: {"attempts": r[1], "avg": round(r[2] or 0, 2)} for r in c.execute(
                "SELECT test_id, COUNT(*), AVG(score * 100.0 / NULLIF(max_score, 0)) FROM test_attempts WHERE series_id=? GROUP BY test_id", (series_id,))}
            leads = c.execute("SELECT COUNT(*) FROM test_leads WHERE series_id=?", (series_id,)).fetchone()[0]
        return {"tests": tests, "leads": leads}

    def leads(self, series_id: str, limit: int = 5000) -> list[dict]:
        with closing(self._conn()) as c:
            c.row_factory = sqlite3.Row
            return [dict(r) for r in c.execute(
                "SELECT created_at, test_id, name, phone, email FROM test_leads WHERE series_id=? ORDER BY id DESC LIMIT ?", (series_id, limit))]


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
               publish: dict, content: dict | None = None, space_id: str | None = None) -> None:
        row = {"exam_id": exam_id, "subject": subject, "topic": topic, "content_type": content_type,
               "run_dir": run_dir, "status": status, "publish": publish, "content": content}
        if space_id:
            row["space_id"] = space_id
        try:
            self.db.insert("runs", row)
        except Exception as e:
            if "space_id" not in row or "space_id" not in str(e):
                raise
            log.warning("runs.space_id column missing - run supabase/schema.sql again to add it; saving without it")
            row.pop("space_id")
            self.db.insert("runs", row)

    def space_topics(self, space_id: str, exam_ids: list[str], limit: int = 40) -> list[dict]:
        cols = "created_at,exam_id,subject,topic,content_type"
        base = {"select": cols, "topic": "neq.", "content_type": "neq.akhbar", "order": "id.desc", "limit": str(limit)}
        exams = ",".join(f'"{e}"' for e in exam_ids)
        try:
            rows = self.db.select("runs", {**base, "or": f"(space_id.eq.{space_id},and(space_id.is.null,exam_id.in.({exams})))"})
        except Exception as e:
            if "space_id" not in str(e):
                raise
            rows = self.db.select("runs", {**base, "exam_id": f"in.({exams})"})  # schema not updated yet
        return [r for r in rows if r.get("topic")]

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
        params = {"select": "id,created_at,exam_id,space_id,subject,topic,content_type,run_dir,status,publish", "order": "id.desc", "limit": str(limit)}
        try:
            rows = self.db.select("runs", params)
        except Exception as e:
            if "space_id" not in str(e):
                raise
            rows = self.db.select("runs", {**params, "select": params["select"].replace("space_id,", "")})
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

    # ---- test series
    def add_lead(self, series_id: str, test_id: str, name: str, phone: str, email: str = "") -> int:
        rows = self.db.insert("test_leads", {"series_id": series_id, "test_id": test_id, "name": name, "phone": phone, "email": email})
        return int(rows[0]["id"]) if rows else 0

    def add_attempt(self, row: dict) -> None:
        self.db.insert("test_attempts", {**{k: row.get(k) for k in ATTEMPT_COLS}, "answers": row.get("answers") or {}})

    def test_scores(self, series_id: str, test_id: str) -> list[float]:
        rows = self.db.select("test_attempts", {"select": "score", "series_id": f"eq.{series_id}", "test_id": f"eq.{test_id}", "limit": "10000"})
        return [r["score"] for r in rows if r.get("score") is not None]

    def series_stats(self, series_id: str) -> dict:
        rows = self.db.select("test_attempts", {"select": "test_id,score,max_score", "series_id": f"eq.{series_id}", "limit": "20000"})
        tests: dict = {}
        for r in rows:
            t = tests.setdefault(r["test_id"], {"attempts": 0, "_sum": 0.0})
            t["attempts"] += 1
            t["_sum"] += (r["score"] or 0) * 100.0 / (r["max_score"] or 1)
        for t in tests.values():
            t["avg"] = round(t.pop("_sum") / t["attempts"], 2)
        return {"tests": tests, "leads": self.db.count("test_leads", {"series_id": f"eq.{series_id}"})}

    def leads(self, series_id: str, limit: int = 5000) -> list[dict]:
        return self.db.select("test_leads", {"select": "created_at,test_id,name,phone,email", "series_id": f"eq.{series_id}",
                                             "order": "id.desc", "limit": str(limit)})


# Backwards compatible name
History = SQLiteHistory


def get_history():
    """Supabase if configured in .env, else the local SQLite file."""
    from .config import settings
    from .supabase_db import configured

    if configured():
        return SupabaseHistory()
    return SQLiteHistory(settings.db_path)
