"""Minimal Supabase (PostgREST) client over httpx.

Uses SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY from .env. The service-role key is a
server secret that bypasses Row Level Security – never put it in a browser or app.
Tables are created by supabase/schema.sql (see `python -m pathshala_bot db-check`).
"""

from __future__ import annotations

import logging

import httpx

from .config import settings

log = logging.getLogger(__name__)

TABLES = ("runs", "headlines", "app_config")


class SupabaseError(RuntimeError):
    pass


def configured() -> bool:
    return bool(settings.supabase_url and settings.supabase_key)


class Supabase:
    def __init__(self, url: str | None = None, key: str | None = None, transport: httpx.BaseTransport | None = None):
        url = (url or settings.supabase_url).rstrip("/")
        key = key or settings.supabase_key
        if not (url and key):
            raise SupabaseError("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env")
        self.http = httpx.Client(
            base_url=f"{url}/rest/v1",
            headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            timeout=30,
            transport=transport,
        )

    def _check(self, r: httpx.Response) -> httpx.Response:
        if r.status_code >= 400:
            try:
                body = r.json()
                msg = body.get("message") or body
                hint = body.get("hint") or ""
            except ValueError:
                msg, hint = r.text[:300], ""
            if r.status_code == 404 or "does not exist" in str(msg) or "schema cache" in str(msg):
                hint = (hint + " " if hint else "") + "Run supabase/schema.sql in the Supabase SQL editor (or `python -m pathshala_bot db-init`)."
            if r.status_code in (401, 403) or "permission denied" in str(msg):
                hint = (hint + " " if hint else "") + "Use the service_role key (Project Settings → API), not the anon key."
            raise SupabaseError(f"Supabase {r.request.method} {r.request.url.path} failed ({r.status_code}): {msg} {hint}".strip())
        return r

    def select(self, table: str, params: dict | None = None) -> list[dict]:
        return self._check(self.http.get(f"/{table}", params=params or {})).json()

    def count(self, table: str, params: dict | None = None) -> int:
        r = self._check(self.http.get(f"/{table}", params={"select": "id", **(params or {})},
                                      headers={"Prefer": "count=exact", "Range": "0-0"}))
        total = r.headers.get("content-range", "*/0").split("/")[-1]
        return int(total) if total.isdigit() else len(r.json())

    def insert(self, table: str, rows: dict | list[dict]) -> list[dict]:
        r = self.http.post(f"/{table}", json=rows, headers={"Prefer": "return=representation"})
        return self._check(r).json()

    def upsert(self, table: str, row: dict, on_conflict: str = "id") -> list[dict]:
        r = self.http.post(f"/{table}", json=row, params={"on_conflict": on_conflict},
                           headers={"Prefer": "resolution=merge-duplicates,return=representation"})
        return self._check(r).json()

    def check(self) -> dict[str, str]:
        """Status of each table: 'ok' or the error."""
        out = {}
        for t in TABLES:
            try:
                self.select(t, {"select": "*", "limit": "1"})
                out[t] = "ok"
            except SupabaseError as e:
                out[t] = str(e)
        return out


def init_schema(db_url: str | None = None) -> None:
    """Apply supabase/schema.sql over a direct Postgres connection (SUPABASE_DB_URL)."""
    from .config import PROJECT_DIR

    db_url = db_url or settings.supabase_db_url
    if not db_url:
        raise SupabaseError("Set SUPABASE_DB_URL (Project Settings → Database → Connection string, URI) "
                            "or paste supabase/schema.sql into the Supabase SQL editor.")
    try:
        import psycopg
    except ImportError as e:
        raise SupabaseError("pip install 'psycopg[binary]' to run db-init (or use the SQL editor)") from e
    sql = (PROJECT_DIR / "supabase" / "schema.sql").read_text(encoding="utf-8")
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute(sql)
