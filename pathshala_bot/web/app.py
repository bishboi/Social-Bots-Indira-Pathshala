"""Admin panel: configure spaces, channels, exams, newspaper and settings; watch runs.

    python -m pathshala_bot serve            # UI + scheduler + Telegram bot in one process

Protected by a login page + signed session cookie (ADMIN_USER / ADMIN_PASSWORD), see auth.py.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .. import control
from ..config import settings
from ..scheduler import Scheduler
from ..supabase_db import configured as supabase_configured
from ..store import get_history
from . import auth

log = logging.getLogger(__name__)
WEB_DIR = Path(__file__).resolve().parent


def current_user(request: Request) -> str | None:
    return auth.read_token(request.cookies.get(auth.COOKIE)) or auth.check_basic_header(request.headers.get("authorization"))


def require_admin(request: Request) -> str:
    """API guard. Answers 401 *without* a WWW-Authenticate header, so browsers never pop up a dialog."""
    if not auth.admin_password():
        raise HTTPException(503, "ADMIN_PASSWORD is not set. Add ADMIN_PASSWORD=... to .env and restart the server "
                                 "(with Docker: docker rm -f pathshala, then docker run ... again).")
    user = current_user(request)
    if not user:
        raise HTTPException(401, "Not logged in")
    return user


def _client_addr(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def create_app(with_bot: bool = True) -> FastAPI:
    sched = Scheduler()
    state: dict = {"bot": None}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        import asyncio

        sched.start()

        async def retry_until_loaded():  # e.g. Supabase tables created after the server started
            while sched.error:
                await asyncio.sleep(60)
                sched.try_reload()

        retry = asyncio.create_task(retry_until_loaded())
        if with_bot:
            state["bot"] = await _start_bot()
        yield
        retry.cancel()
        if state["bot"] is not None:
            await _stop_bot(state["bot"])
        sched.shutdown()

    app = FastAPI(title="Pathshala admin", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    guard = [Depends(require_admin)]

    @app.get("/healthz")
    def healthz():
        """Unauthenticated liveness check (used by the Docker HEALTHCHECK). Reveals no settings."""
        return {"ok": True, "settings_loaded": sched.error is None}

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        if not auth.admin_password() or not current_user(request):
            return RedirectResponse("/login", status_code=303)
        return HTMLResponse((WEB_DIR / "index.html").read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, error: str = ""):
        if auth.admin_password() and current_user(request):
            return RedirectResponse("/", status_code=303)
        return HTMLResponse(_login_html(error, missing_password=not auth.admin_password()), headers={"Cache-Control": "no-store"})

    @app.post("/login")
    async def login(request: Request):
        from urllib.parse import parse_qs

        if not auth.admin_password():
            return RedirectResponse("/login", status_code=303)
        addr = _client_addr(request)
        wait = auth.limiter.blocked(addr)
        if wait:
            return RedirectResponse(f"/login?error=locked&wait={wait}", status_code=303)
        form = parse_qs((await request.body()).decode("utf-8", "replace"))
        user = (form.get("username") or [""])[0]
        password = (form.get("password") or [""])[0]
        if not auth.check_password(user, password):
            auth.limiter.fail(addr)
            import asyncio

            await asyncio.sleep(1)  # slow down guessing
            return RedirectResponse("/login?error=wrong", status_code=303)
        auth.limiter.reset(addr)
        resp = RedirectResponse("/", status_code=303)
        secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        resp.set_cookie(auth.COOKIE, auth.make_token(user), max_age=auth.SESSION_DAYS * 86400,
                        httponly=True, samesite="lax", secure=secure, path="/")
        return resp

    @app.post("/logout")
    def logout():
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(auth.COOKIE, path="/")
        return resp

    @app.get("/api/config", dependencies=guard)
    def get_config():
        try:
            cfg = control.load()
        except Exception as e:
            raise HTTPException(500, f"Could not load settings: {e}") from e
        defs = control.all_exam_defs(cfg)
        return {
            "config": control.public_view(cfg),
            "catalogue": control.catalogue_info(),
            "exam_defs": {k: {"name": e.name, "short_name": e.short_name, "language": e.language, "audience": e.audience,
                              "subjects": list(e.subjects), "custom": k in {c.id for c in cfg.custom_exams}} for k, e in defs.items()},
        }

    @app.put("/api/config", dependencies=guard)
    async def put_config(request: Request):
        body = await request.json()
        try:
            new = control.merge_from_ui(control.load(), body)
        except Exception as e:  # validation errors go back to the form
            raise HTTPException(400, str(e)) from e
        control.save(new)
        sched.reload()
        return get_config()

    @app.get("/api/status", dependencies=guard)
    def status():
        try:
            runs = get_history().runs(40)
        except Exception as e:  # surface database problems on the dashboard instead of a 500
            log.exception("history unavailable")
            runs, db_error = [], str(e)[:500]
        else:
            db_error = None
        for r in runs:
            d = Path(r.get("run_dir") or "")
            r["run"] = d.name
            r["files"] = sorted(p.name for p in d.glob("*") if p.suffix in (".pdf", ".mp4", ".png")) if d.is_dir() else []
        return {"jobs": sched.jobs(), "worker": sched.state(), "runs": runs, "bot": bool(state["bot"]),
                "startup_error": sched.error,
                "anthropic_key": bool(os.getenv("ANTHROPIC_API_KEY")), "openai_key": bool(os.getenv("OPENAI_API_KEY")), "openrouter_key": bool(settings.openrouter_api_key),
                "provider": settings.llm_provider, "database": "supabase" if supabase_configured() else "sqlite",
                "db_error": db_error}

    @app.post("/api/exams/lookup", dependencies=guard)
    async def exam_lookup(request: Request):
        """Typed exam name -> a known exam, or a new profile researched on the web (not saved until the panel saves)."""
        import asyncio

        from .. import exam_research

        body = await request.json()
        cfg = control.load()
        if body.get("custom_exams") is not None:  # unsaved exams in the browser count as known
            for ce in body["custom_exams"]:
                try:
                    c = control.CustomExam.model_validate(ce)
                except Exception:
                    continue
                if all(x.id != c.id for x in cfg.custom_exams):
                    cfg.custom_exams.append(c)
        control.apply(cfg)
        try:
            return await asyncio.to_thread(exam_research.lookup, cfg, str(body.get("name", "")), body.get("refresh_id"))
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:  # no API key, provider error...
            log.exception("exam lookup failed")
            from ..providers import friendly_error

            raise HTTPException(502, f"Could not research the exam: {friendly_error(str(e))[:300]}") from e

    @app.post("/api/spaces/{sid}/run", dependencies=guard)
    async def run_space_now(sid: str, dry: bool = False):
        cfg = control.load()
        if cfg.space(sid) is None:
            raise HTTPException(404, "No such space")
        if not control.space_exams(cfg, cfg.space(sid)):
            raise HTTPException(400, "This space has no exams")
        sched.run_space_now(sid, dry_run=dry)
        return {"started": True, "dry_run": dry}

    # ---------------- test series
    from ..testseries import builder as tsb
    from ..testseries import public as tsp

    app.include_router(tsp.router())

    @app.get("/api/series", dependencies=guard)
    def series_status():
        cfg = control.load()
        control.apply(cfg)
        out = {}
        hist = None
        for ts in cfg.test_series:
            bp = tsb.load_blueprint(ts)
            have = tsb.built(ts.id)
            skip = tsb.skipped(ts.id)
            try:
                hist = hist or get_history()
                stats = hist.series_stats(ts.id)
            except Exception as e:  # tables not created yet
                stats = {"tests": {}, "leads": 0, "error": str(e)[:200]}
            specs = tsb.plan(ts, bp) if bp else []
            planned = {s.id for s in specs}
            rows = []
            for spec in specs + [tsb.TestSpec(id=t.id, kind=t.kind, order=t.order, title=t.title, access=t.access)
                                 for t in have.values() if t.id not in planned]:
                t = have.get(spec.id)
                rows.append({"id": spec.id, "kind": spec.kind, "title": spec.title, "planned": spec.id in planned,
                             "questions": len(t.questions) if t else spec.questions, "built": bool(t),
                             "minutes": t.duration_minutes if t else None, "access": t.access if t else spec.access,
                             "note": t.note if t else skip.get(spec.id, ""), "skipped": spec.id in skip, "flagged": sum(1 for q in t.questions if q.flagged) if t else 0,
                             "figures": sum(1 for q in t.questions if q.svg) if t else 0,
                             "published": (t.published or {}).get("at") if t else None,
                             "files": [n for n in ("paper.pdf", "solutions.pdf") if t and (tsb.test_dir(ts.id, t.id) / n).exists()],
                             "url": tsb.test_url(ts.id, spec.id), **(stats["tests"].get(spec.id) or {})})
            out[ts.id] = {"blueprint": bp.model_dump() if bp else None,
                          "summary": tsb.describe_blueprint(bp) if bp else "", "tests": rows,
                          "leads": stats.get("leads", 0), "stats_error": stats.get("error"), "url": tsb.series_url(ts.id)}
        from ..testseries import diagrams

        return {"series": out, "diagrams": diagrams.available(), "public_url": settings.public_base_url}

    @app.post("/api/series/{sid}/run", dependencies=guard)
    async def run_series(sid: str, what: str = "next", dry: bool = False):
        if what not in ("next", "all", "blueprint"):
            raise HTTPException(400, "what must be next, all or blueprint")
        if control.load().series(sid) is None:
            raise HTTPException(404, "No such test series (save first)")
        sched.run_series_now(sid, what=what, dry_run=dry)
        return {"started": True}

    @app.post("/api/series/{sid}/tests/{tid}/publish", dependencies=guard)
    async def publish_test(sid: str, tid: str):
        import asyncio

        cfg = control.load()
        control.apply(cfg)
        ts, test = cfg.series(sid), tsb.load_test(sid, tid)
        if ts is None or test is None:
            raise HTTPException(404, "Not found")
        res = await asyncio.to_thread(tsb.publish, cfg, ts, test)
        return {"summary": res.get("summary", "")}

    @app.delete("/api/series/{sid}/tests/{tid}", dependencies=guard)
    def delete_test(sid: str, tid: str):
        if not tsb.delete_test(sid, tid):
            raise HTTPException(404, "Not found")
        return {"deleted": True}

    @app.get("/api/series/{sid}/leads.csv", dependencies=guard)
    def leads_csv(sid: str):
        import csv
        import io

        from fastapi.responses import Response

        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["created_at", "test_id", "name", "phone", "email"])
        for r in get_history().leads(sid):
            w.writerow([r.get(k, "") for k in ("created_at", "test_id", "name", "phone", "email")])
        return Response(buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{sid}-leads.csv"'})

    @app.get("/series-files/{sid}/{tid}/{name}", dependencies=guard)
    def series_file(sid: str, tid: str, name: str):
        if name not in ("paper.pdf", "solutions.pdf", "test.json") or tsb.load_test(sid, tid) is None:
            raise HTTPException(404)
        return FileResponse(tsb.test_dir(sid, tid) / name)

    @app.post("/api/schedules/{sid}/run", dependencies=guard)
    async def run_now(sid: str, dry: bool = False):
        if control.load().schedule(sid) is None:
            raise HTTPException(404, "No such schedule")
        sched.run_now(sid, dry_run=dry)
        return {"started": True, "dry_run": dry}

    @app.post("/api/channels/{cid}/test", dependencies=guard)
    def test_channel(cid: str):
        cfg = control.load()
        ch = cfg.channel(cid)
        if ch is None:
            raise HTTPException(404, "No such channel")
        try:
            return {"ok": True, "detail": _test_channel(ch)}
        except Exception as e:  # report the reason to the UI
            return {"ok": False, "detail": str(e)[:400]}

    @app.get("/files/{run}/{name}", dependencies=guard)
    def get_file(run: str, name: str):
        base = settings.output_dir.resolve()
        path = (base / run / name).resolve()
        if base not in path.parents or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path)

    return app


def _test_channel(ch: control.Channel) -> str:
    if ch.platform == "telegram":
        from ..publish.telegram import Telegram

        msg = Telegram().send_message(ch.chat_id, f"✅ {settings.brand_name}: channel connected.")
        return f"Test message sent (id {msg['message_id']})"
    if ch.platform == "youtube":
        from ..publish import youtube

        token = youtube.access_token({"client_id": ch.client_id, "client_secret": ch.client_secret, "refresh_token": ch.refresh_token})
        r = httpx.get("https://www.googleapis.com/youtube/v3/channels", params={"part": "snippet", "mine": "true"},
                      headers={"Authorization": f"Bearer {token}"}, timeout=30)
        r.raise_for_status()
        items = r.json().get("items") or []
        return f"Connected to YouTube channel: {items[0]['snippet']['title']}" if items else "Token works, but no channel found"
    r = httpx.get(f"https://graph.facebook.com/{settings.instagram_graph_version}/{ch.user_id}",
                  params={"fields": "username", "access_token": ch.access_token}, timeout=30)
    body = r.json()
    if "error" in body:
        raise RuntimeError(body["error"].get("message", body))
    return f"Connected to Instagram @{body.get('username')}"


async def _start_bot():
    if not settings.telegram_bot_token:
        log.info("no Telegram bot token - bot commands disabled")
        return None
    try:
        from ..bot import build_application

        app = build_application(with_schedule=False)
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        log.info("telegram bot polling")
        return app
    except Exception:
        log.exception("telegram bot failed to start (the panel and scheduler keep running)")
        return None


async def _stop_bot(app):
    try:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
    except Exception:
        log.exception("bot shutdown")


def _login_html(error: str, missing_password: bool) -> str:
    import html

    msg = ""
    if missing_password:
        msg = ("<div class='err'><b>ADMIN_PASSWORD is not set.</b><br>Add <code>ADMIN_PASSWORD=...</code> to <code>.env</code> and restart "
               "the server (Docker: <code>docker rm -f pathshala</code>, then <code>docker run ...</code> again).</div>")
    elif error == "wrong":
        msg = "<div class='err'>Wrong username or password.</div>"
    elif error == "locked":
        msg = "<div class='err'>Too many failed attempts. Please wait a few minutes and try again.</div>"
    brand = html.escape(settings.brand_name)
    disabled = "disabled" if missing_password else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{brand} · Login</title>
<style>
  :root {{ --bg:#f5f6fa; --panel:#fff; --ink:#141833; --muted:#646a86; --line:#e3e6f0; --accent:#ff8a1f; --bad:#d1343b; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --bg:#0f1222; --panel:#171b31; --ink:#e8eaf6; --muted:#9aa0bf; --line:#2a2f4d; }} }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center; padding:16px;
         font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,"Noto Sans Devanagari",sans-serif; background:var(--bg); color:var(--ink); }}
  form {{ width:100%; max-width:360px; background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:24px; }}
  h1 {{ font-size:19px; margin:0 0 4px; }} p {{ margin:0 0 18px; color:var(--muted); font-size:13px; }}
  label {{ display:block; font-size:12px; color:var(--muted); margin:12px 0 4px; }}
  input {{ width:100%; padding:10px 12px; border:1px solid var(--line); border-radius:8px; background:var(--bg); color:var(--ink); font:inherit; }}
  button {{ width:100%; margin-top:18px; padding:10px; border:0; border-radius:8px; background:var(--accent); color:#1a1200; font:600 15px system-ui,sans-serif; cursor:pointer; }}
  button:disabled {{ opacity:.5; cursor:default; }}
  .err {{ border:1px solid var(--bad); background:color-mix(in srgb, var(--bad) 12%, var(--panel)); border-radius:8px; padding:9px 12px; font-size:13px; margin-bottom:6px; }}
  code {{ font-size:12px; }}
</style></head><body>
<form method="post" action="/login" autocomplete="on">
  <h1>{brand} · Control</h1><p>Sign in to manage channels, exams and schedules.</p>
  {msg}
  <label for="u">Username</label><input id="u" name="username" value="{html.escape(auth.admin_user())}" autocomplete="username" required {disabled}>
  <label for="p">Password</label><input id="p" name="password" type="password" autocomplete="current-password" required autofocus {disabled}>
  <button type="submit" {disabled}>Sign in</button>
</form></body></html>"""
