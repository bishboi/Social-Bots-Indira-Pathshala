"""Minimal Supabase (PostgREST) client over httpx.

Uses SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY from .env. Either key style works:
- new Secret key  `sb_secret_...`  (Project Settings → API Keys → Secret keys) – sent in the
  `apikey` header only, because it is not a JWT and is rejected as `Authorization: Bearer`;
- legacy `service_role` JWT  `eyJ...`  (API Keys → Legacy API keys) – sent in both headers.
Both bypass Row Level Security: server secrets, never put them in a browser or app.
Tables are created by supabase/schema.sql (see `python -m pathshala_bot db-check`).
"""

from __future__ import annotations

import base64
import json
import logging
import re

import httpx

from .config import settings

log = logging.getLogger(__name__)

TABLES = ("runs", "headlines", "app_config")


class SupabaseError(RuntimeError):
    pass


def configured() -> bool:
    return bool(settings.supabase_url and settings.supabase_key)


def normalize_url(url: str) -> str:
    """'abc.supabase.co/rest/v1/' -> 'https://abc.supabase.co'."""
    url = (url or "").strip().rstrip("/")
    for suffix in ("/rest/v1", "/rest/v1/"):
        if url.endswith(suffix.rstrip("/")):
            url = url[: -len(suffix.rstrip("/"))].rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def project_ref(url: str) -> str | None:
    m = re.match(r"https?://([a-z0-9]+)\.supabase\.(co|in)\b", normalize_url(url))
    return m.group(1) if m else None


def _jwt_claims(key: str) -> dict | None:
    parts = key.split(".")
    if len(parts) != 3:
        return None
    try:
        return json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except (ValueError, json.JSONDecodeError):
        return None


def key_kind(key: str) -> str:
    """secret | publishable | service_role | anon | jwt:<role> | unknown"""
    key = (key or "").strip()
    if key.startswith("sb_secret_"):
        return "secret"
    if key.startswith("sb_publishable_"):
        return "publishable"
    claims = _jwt_claims(key)
    if claims:
        role = claims.get("role")
        return role if role in ("service_role", "anon") else f"jwt:{role}"
    return "unknown"


def diagnose(url: str | None = None, key: str | None = None) -> list[str]:
    """Problems we can spot in the settings without calling Supabase."""
    url = normalize_url(url if url is not None else settings.supabase_url)
    key = (key if key is not None else settings.supabase_key or "").strip()
    problems = []
    if not url:
        problems.append("SUPABASE_URL is empty. Use the Project URL, e.g. https://abcd1234.supabase.co (Project Settings → API).")
    elif not project_ref(url):
        problems.append(f"SUPABASE_URL '{url}' doesn't look like https://<project-ref>.supabase.co (fine only for self-hosted Supabase).")
    if not key:
        problems.append("SUPABASE_SERVICE_ROLE_KEY is empty.")
        return problems
    if any(c.isspace() for c in key) or key[0] in "'\"" or key[-1] in "'\"":
        problems.append("The key contains spaces or quotes - paste only the key itself.")
    kind = key_kind(key)
    if kind == "publishable":
        problems.append("This is the Publishable key (sb_publishable_...), which is public and read-only. Use a Secret key "
                        "(sb_secret_..., Project Settings → API Keys → Secret keys) or the legacy service_role key.")
    elif kind == "anon":
        problems.append("This is the anon (public) key. Use the service_role key (API Keys → Legacy API keys) or a Secret key (sb_secret_...).")
    elif kind == "unknown":
        problems.append("The key is neither a Secret key (sb_secret_...) nor a legacy JWT (eyJ...). It may be truncated or the wrong value.")
    claims = _jwt_claims(key)
    ref = project_ref(url)
    if claims and ref and claims.get("ref") and claims["ref"] != ref:
        problems.append(f"The key belongs to a different project ('{claims['ref']}') than SUPABASE_URL ('{ref}'). "
                        "Copy the URL and the key from the same project.")
    return problems


class Supabase:
    def __init__(self, url: str | None = None, key: str | None = None, transport: httpx.BaseTransport | None = None):
        url = normalize_url(url or settings.supabase_url)
        key = (key or settings.supabase_key or "").strip()
        if not (url and key):
            raise SupabaseError("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env")
        self.url, self.key = url, key
        headers = {"apikey": key, "Content-Type": "application/json"}
        if not key.startswith("sb_"):
            # legacy JWT keys go in both headers; new sb_ keys are not JWTs and are rejected as Bearer tokens
            headers["Authorization"] = f"Bearer {key}"
        self.http = httpx.Client(base_url=f"{url}/rest/v1", headers=headers, timeout=30, transport=transport)

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
                found = diagnose(self.url, self.key)
                if found:
                    hint = " ".join(found)
                elif key_kind(self.key) == "secret":
                    hint = ("Supabase rejected this Secret key. Check it was copied completely from this project "
                            "(Project Settings → API Keys → Secret keys) and isn't revoked; if it still fails, use the legacy "
                            "service_role key (API Keys → Legacy API keys) in SUPABASE_SERVICE_ROLE_KEY.")
                else:
                    hint = ("Use a Secret key (sb_secret_...) or the legacy service_role key from this project "
                            "(Project Settings → API Keys), not the anon/publishable key.")
            raise SupabaseError(f"Supabase {r.request.method} {r.request.url.path} failed ({r.status_code}): {msg} {hint}".strip())
        return r

    def _send(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            return self._check(self.http.request(method, path, **kw))
        except httpx.HTTPError as e:  # DNS / network / timeout
            raise SupabaseError(f"Cannot reach Supabase at {self.url} ({e.__class__.__name__}: {e}). "
                                "Check SUPABASE_URL (https://<project>.supabase.co) and the server's internet access.") from e

    def select(self, table: str, params: dict | None = None) -> list[dict]:
        return self._send("GET", f"/{table}", params=params or {}).json()

    def count(self, table: str, params: dict | None = None) -> int:
        r = self._send("GET", f"/{table}", params={"select": "id", **(params or {})},
                       headers={"Prefer": "count=exact", "Range": "0-0"})
        total = r.headers.get("content-range", "*/0").split("/")[-1]
        return int(total) if total.isdigit() else len(r.json())

    def insert(self, table: str, rows: dict | list[dict]) -> list[dict]:
        return self._send("POST", f"/{table}", json=rows, headers={"Prefer": "return=representation"}).json()

    def upsert(self, table: str, row: dict, on_conflict: str = "id") -> list[dict]:
        return self._send("POST", f"/{table}", json=row, params={"on_conflict": on_conflict},
                          headers={"Prefer": "resolution=merge-duplicates,return=representation"}).json()

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
