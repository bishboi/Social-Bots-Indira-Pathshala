"""Admin panel: configure channels, exams, schedules and settings; watch runs.

    python -m pathshala_bot serve            # UI + scheduler + Telegram bot in one process

Protected with HTTP Basic auth (ADMIN_USER / ADMIN_PASSWORD).
"""

from __future__ import annotations

import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from .. import control
from ..config import settings
from ..scheduler import Scheduler
from ..store import History

log = logging.getLogger(__name__)
WEB_DIR = Path(__file__).resolve().parent
security = HTTPBasic(realm="Pathshala admin")


def require_admin(creds: HTTPBasicCredentials = Depends(security)) -> str:
    user = os.getenv("ADMIN_USER", "admin")
    password = os.getenv("ADMIN_PASSWORD", "")
    if not password:
        raise HTTPException(503, "Set ADMIN_PASSWORD to use the admin panel")
    ok = secrets.compare_digest(creds.username.encode(), user.encode()) and secrets.compare_digest(creds.password.encode(), password.encode())
    if not ok:
        raise HTTPException(401, "Wrong username or password", headers={"WWW-Authenticate": "Basic"})
    return creds.username


def create_app(with_bot: bool = True) -> FastAPI:
    sched = Scheduler()
    state: dict = {"bot": None}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        sched.start()
        if with_bot:
            state["bot"] = await _start_bot()
        yield
        if state["bot"] is not None:
            await _stop_bot(state["bot"])
        sched.shutdown()

    app = FastAPI(title="Pathshala admin", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    guard = [Depends(require_admin)]

    @app.get("/", response_class=HTMLResponse, dependencies=guard)
    def index():
        return (WEB_DIR / "index.html").read_text(encoding="utf-8")

    @app.get("/api/config", dependencies=guard)
    def get_config():
        cfg = control.load()
        defs = control.all_exam_defs(cfg)
        return {
            "config": control.public_view(cfg),
            "catalogue": control.catalogue_info(),
            "exam_defs": {k: {"name": e.name, "short_name": e.short_name, "language": e.language,
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
        runs = History(settings.db_path).runs(40)
        for r in runs:
            d = Path(r.get("run_dir") or "")
            r["run"] = d.name
            r["files"] = sorted(p.name for p in d.glob("*") if p.suffix in (".pdf", ".mp4", ".png")) if d.is_dir() else []
        return {"jobs": sched.jobs(), "worker": sched.state(), "runs": runs, "bot": bool(state["bot"]),
                "anthropic_key": bool(os.getenv("ANTHROPIC_API_KEY"))}

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
