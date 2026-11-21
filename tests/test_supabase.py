"""Supabase backend against an in-memory fake PostgREST server (no network)."""

from __future__ import annotations

import json
from datetime import date, timedelta

import httpx
import pytest

from pathshala_bot import supabase_db
from pathshala_bot.config import settings

_REAL_SUPABASE = supabase_db.Supabase


class FakePostgREST:
    """Implements the subset of PostgREST the app uses: eq/neq/gte/not.is.null, order, limit, count, upsert."""

    def __init__(self, tables=("runs", "headlines", "app_config")):
        self.tables = {t: [] for t in tables}
        self.ids = {t: 0 for t in tables}
        self.requests = []

    def _match(self, row, col, expr):
        val = row.get(col)
        op, _, arg = expr.partition(".")
        if op == "eq":
            return str(val) == arg
        if op == "neq":
            return val is not None and str(val) != arg
        if op == "gte":
            return val is not None and str(val) >= arg
        if expr == "not.is.null":
            return val is not None
        raise AssertionError(f"unsupported filter {col}={expr}")

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["apikey"] == "service-key" and request.headers["authorization"] == "Bearer service-key"
        table = request.url.path.rsplit("/", 1)[-1]
        if table not in self.tables:
            return httpx.Response(404, json={"message": f"relation \"public.{table}\" does not exist", "code": "42P01"})
        params = dict(request.url.params)
        rows = self.tables[table]
        if request.method == "GET":
            select = params.pop("select", "*")
            order = params.pop("order", None)
            limit = params.pop("limit", None)
            out = [r for r in rows if all(self._match(r, c, e) for c, e in params.items())]
            if order:
                col, _, direction = order.partition(".")
                out.sort(key=lambda r: r.get(col) or 0, reverse=direction == "desc")
            total = len(out)
            if limit:
                out = out[: int(limit)]
            if "count=exact" in request.headers.get("prefer", ""):
                return httpx.Response(206, json=out[:1], headers={"content-range": f"0-0/{total}"})
            if select != "*":
                out = [{k: r.get(k) for k in select.split(",")} for r in out]
            return httpx.Response(200, json=out)
        if request.method == "POST":
            body = json.loads(request.content)
            items = body if isinstance(body, list) else [body]
            upsert = "merge-duplicates" in request.headers.get("prefer", "")
            created = []
            for item in items:
                existing = next((r for r in rows if upsert and r.get("id") == item.get("id")), None)
                if existing:
                    existing.update(item)
                    created.append(existing)
                    continue
                if "id" not in item:
                    self.ids[table] += 1
                    item = {"id": self.ids[table], "created_at": f"2026-11-21T00:00:{self.ids[table]:02d}Z", **item}
                rows.append(item)
                created.append(item)
            return httpx.Response(201, json=created)
        return httpx.Response(405)


@pytest.fixture
def fake(monkeypatch, tmp_path):
    server = FakePostgREST()
    real = supabase_db.Supabase

    monkeypatch.setattr(supabase_db, "Supabase", lambda *a, **k: real("https://x.supabase.co", "service-key", transport=httpx.MockTransport(server)))
    saved = dict(vars(settings))
    object.__setattr__(settings, "supabase_url", "https://x.supabase.co")
    object.__setattr__(settings, "supabase_key", "service-key")
    object.__setattr__(settings, "output_dir", tmp_path)
    monkeypatch.setenv("CONTROL_FILE", str(tmp_path / "control.json"))
    yield server
    for k, v in saved.items():
        object.__setattr__(settings, k, v)


def test_get_history_picks_backend(fake, tmp_path):
    from pathshala_bot import store

    assert store.get_history().backend == "supabase"
    object.__setattr__(settings, "supabase_url", "")
    object.__setattr__(settings, "db_path", tmp_path / "h.sqlite3")
    assert store.get_history().backend == "sqlite"


def test_supabase_history_roundtrip(fake):
    from pathshala_bot.store import SupabaseHistory

    h = SupabaseHistory()
    h.record("rajasthan-si", "History", "Integration of Rajasthan", "cheatsheet", "/out/1", "ok", {"telegram": {"messages": [1]}},
             content={"mcqs": [{"question": "q?"}]})
    h.record("rajasthan-si", "Geo", "", "pyq_quiz", "/out/2", "failed", {})
    h.record("jee-main", "Physics", "Moment of inertia", "formula_sheet", "/out/3", "ok", {})
    h.record("akhbar:rajasthan-hi", "राजस्थान संस्करण", "Lead", "akhbar", "/out/4", "ok", {}, content={"articles": []})
    h.record("akhbar:rajasthan-hi", "राजस्थान संस्करण", "Lead 2", "akhbar", "/out/5", "failed", {})

    assert h.recent_topics("rajasthan-si") == ["Integration of Rajasthan"]  # empty topics skipped
    assert h.akhbar_count("akhbar:rajasthan-hi") == 1  # failed editions don't count
    runs = h.runs(10)
    assert runs[0]["exam_id"] == "akhbar:rajasthan-hi" and json.loads(runs[-1]["publish"]) == {"telegram": {"messages": [1]}}
    assert h.last_runs(2)[0][2] == "Lead 2"
    contents = h.recent_contents("rajasthan-si")
    assert contents == [{"mcqs": [{"question": "q?"}]}]  # newspapers and empty content excluded
    assert len(h.recent_contents()) == 1

    today = date.today()
    h.add_headlines("akhbar:rajasthan-hi", today.isoformat(), ["A", "B"])
    h.add_headlines("akhbar:rajasthan-hi", (today - timedelta(days=30)).isoformat(), ["Old"])
    assert set(h.recent_headlines("akhbar:rajasthan-hi")) == {"A", "B"}


def test_control_settings_stored_in_supabase(fake, tmp_path):
    from pathshala_bot import control

    assert control.storage() == "supabase"
    cfg = control.load()  # seeds from .env into app_config
    assert len(fake.tables["app_config"]) == 1
    cfg.general.brand_name = "From DB"
    cfg.secrets.openai_api_key = "sk-secret"
    control.save(cfg)
    assert len(fake.tables["app_config"]) == 1 and fake.tables["app_config"][0]["data"]["general"]["brand_name"] == "From DB"
    assert control.load().secrets.openai_api_key == "sk-secret"
    assert not (tmp_path / "control.json").exists()  # nothing written to disk


def test_existing_control_json_is_imported(fake, tmp_path):
    from pathshala_bot import control

    seeded = control._seed_from_env()
    seeded.general.brand_name = "Local file brand"
    (tmp_path / "control.json").write_text(seeded.model_dump_json(), encoding="utf-8")
    assert control.load().general.brand_name == "Local file brand"
    assert fake.tables["app_config"][0]["data"]["general"]["brand_name"] == "Local file brand"


def test_helpful_errors(monkeypatch):
    def missing(request):
        return httpx.Response(404, json={"message": 'relation "public.runs" does not exist'})

    db = supabase_db.Supabase("https://x.supabase.co", "k", transport=httpx.MockTransport(missing))
    with pytest.raises(supabase_db.SupabaseError, match="schema.sql"):
        db.select("runs")

    def denied(request):
        return httpx.Response(401, json={"message": "permission denied for table runs"})

    db = supabase_db.Supabase("https://x.supabase.co", "k", transport=httpx.MockTransport(denied))
    with pytest.raises(supabase_db.SupabaseError, match="truncated"):  # "k" is not a valid key of any kind
        db.select("runs")


def test_db_check(fake):
    status = supabase_db.Supabase().check()
    assert status == {"runs": "ok", "headlines": "ok", "app_config": "ok"}


def test_schema_file_matches_tables():
    from pathshala_bot.config import PROJECT_DIR

    sql = (PROJECT_DIR / "supabase" / "schema.sql").read_text()
    for t in supabase_db.TABLES:
        assert f"create table if not exists public.{t}" in sql
        assert f"alter table public.{t}" in sql and "enable row level security" in sql
    for col in ("exam_id", "topic", "content_type", "status", "publish", "content"):
        assert f"  {col}" in sql


def test_pipeline_run_is_recorded_in_supabase(fake, tmp_path):
    from pathlib import Path

    from pathshala_bot import pipeline

    fixture = Path(__file__).parent / "fixtures" / "rajasthan_si_sample.json"
    res = pipeline.run(exam_id="rajasthan-si", from_json=fixture, make_video=False)
    assert res.ok
    row = fake.tables["runs"][-1]
    assert row["exam_id"] == "rajasthan-si" and row["status"] == "ok"
    assert row["content"]["mcqs"][0]["correct_index"] == 0  # whole package stored as JSON for /quiz


def _jwt(role, ref):
    import base64

    b = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    return f"{b({'alg': 'HS256'})}.{b({'role': role, 'ref': ref})}.sig"


def test_new_secret_keys_are_not_sent_as_bearer():
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(200, json=[])

    supabase_db.Supabase("https://abcd1234.supabase.co", "sb_secret_xyz", transport=httpx.MockTransport(handler)).select("runs")
    assert seen["apikey"] == "sb_secret_xyz" and "authorization" not in seen
    seen.clear()
    legacy = _jwt("service_role", "abcd1234")
    supabase_db.Supabase("https://abcd1234.supabase.co", legacy, transport=httpx.MockTransport(handler)).select("runs")
    assert seen["apikey"] == legacy and seen["authorization"] == f"Bearer {legacy}"


def test_key_and_url_diagnosis():
    d = supabase_db.diagnose
    assert supabase_db.normalize_url("abcd1234.supabase.co/rest/v1/") == "https://abcd1234.supabase.co"
    assert d("https://abcd1234.supabase.co", "sb_secret_x") == []
    assert d("https://abcd1234.supabase.co", _jwt("service_role", "abcd1234")) == []
    assert "Publishable" in d("https://abcd1234.supabase.co", "sb_publishable_x")[0]
    assert "anon" in d("https://abcd1234.supabase.co", _jwt("anon", "abcd1234"))[0]
    assert "different project" in " ".join(d("https://abcd1234.supabase.co", _jwt("service_role", "other999")))
    assert "truncated" in d("https://abcd1234.supabase.co", "nonsense")[0]


def test_invalid_key_error_names_the_cause():
    def reject(request):
        return httpx.Response(401, json={"message": "Invalid API key"})

    db = supabase_db.Supabase("https://abcd1234.supabase.co", "sb_publishable_x", transport=httpx.MockTransport(reject))
    with pytest.raises(supabase_db.SupabaseError, match="Publishable key"):
        db.select("app_config")
    db = supabase_db.Supabase("https://abcd1234.supabase.co", "sb_secret_x", transport=httpx.MockTransport(reject))
    with pytest.raises(supabase_db.SupabaseError, match="legacy service_role"):
        db.select("app_config")


def test_db_setup_seeds_settings(fake, capsys, monkeypatch):
    from pathshala_bot.__main__ import db_setup

    object.__setattr__(settings, "supabase_url", "https://abcd1234.supabase.co")
    object.__setattr__(settings, "supabase_key", "sb_secret_test")

    def gateway(request):  # like Supabase: sb_ keys arrive in `apikey` only; then hand over to the fake DB
        assert "authorization" not in request.headers and request.headers["apikey"] == "sb_secret_test"
        request.headers["apikey"] = "service-key"
        request.headers["authorization"] = "Bearer service-key"
        return fake(request)

    monkeypatch.setattr(supabase_db, "Supabase", lambda *a, **k: _REAL_SUPABASE(
        "https://abcd1234.supabase.co", "sb_secret_test", transport=httpx.MockTransport(gateway)))
    assert db_setup() == 0
    out = capsys.readouterr().out
    assert "Secret key" in out and "Created settings from .env" in out and "Supabase is ready" in out
    assert len(fake.tables["app_config"]) == 1
    assert db_setup() == 0 and "kept your saved settings" in capsys.readouterr().out
