"""CLI.

  python -m pathshala_bot run [--exam rajasthan-si] [--type pyq_quiz] [--publish telegram,youtube,instagram]
  python -m pathshala_bot run --from-json output/<run>/content.json --publish telegram   # re-render / re-post
  python -m pathshala_bot serve        # admin UI + scheduler + Telegram bot (set ADMIN_PASSWORD)
  python -m pathshala_bot akhbar --region Rajasthan --language hi [--publish]   # daily current-affairs paper
  python -m pathshala_bot db-check     # check the Supabase database (or local SQLite)
  python -m pathshala_bot bot          # bot only, legacy .env schedule
  python -m pathshala_bot exams        # list exams
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import load_exams
from .models import CONTENT_TYPES, VIDEO_STYLES


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "fontTools", "weasyprint"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    ap = argparse.ArgumentParser(prog="pathshala_bot")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run the content pipeline once")
    r.add_argument("--exam", help="exam id from exams.yaml (default: weighted random)")
    r.add_argument("--type", choices=list(CONTENT_TYPES), help="content format (default: random)")
    r.add_argument("--subject", help="restrict to one subject of the exam")
    r.add_argument("--publish", default="", help="comma list: telegram,youtube,instagram or 'all'. Empty = dry run")
    r.add_argument("--chat", help="override Telegram chat id")
    r.add_argument("--from-json", type=Path, help="skip research/generation and use an existing content.json")
    r.add_argument("--video-style", choices=list(VIDEO_STYLES), help="short video style (default: chosen to fit the topic)")
    r.add_argument("--no-video", action="store_true", help="skip the short video")
    r.add_argument("--seed", type=int, help="random seed for reproducible picks")

    sv = sub.add_parser("serve", help="admin UI + scheduler + Telegram bot (recommended)")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8080)
    sv.add_argument("--no-bot", action="store_true", help="don't start Telegram bot polling")

    ak = sub.add_parser("akhbar", help="build today's current-affairs newspaper (one edition = a state or central + a language)")
    ak.add_argument("--edition", help="edition id from the admin panel, e.g. rajasthan-hi, central-en")
    ak.add_argument("--region", help="'central' or a state/UT name, e.g. Rajasthan, 'Uttar Pradesh'")
    ak.add_argument("--language", choices=["hi", "en", "hinglish"], default="hi")
    ak.add_argument("--publish", action="store_true", help="post to the edition's Telegram channels from the admin config")
    ak.add_argument("--chat", help="post to this Telegram chat instead")
    ak.add_argument("--from-json", type=Path, help="render an existing akhbar.json (no AI calls)")

    sub.add_parser("db-check", help="check the database (Supabase or local SQLite)")
    sub.add_parser("db-init", help="create the Supabase tables (needs SUPABASE_DB_URL), same as running supabase/schema.sql")

    sub.add_parser("bot", help="Telegram bot only, with the legacy POST_TIMES schedule")
    sub.add_parser("exams", help="list configured exams")

    a = ap.parse_args(argv)
    if a.cmd == "exams":
        for e in load_exams().values():
            print(f"{e.id:16} {e.language:9} w={e.weight:<4} {e.name}  [{', '.join(e.subjects)}]")
        return 0
    if a.cmd == "serve":
        import os

        import uvicorn

        from . import control
        from .web.app import create_app

        control.apply(control.load())
        if not os.getenv("ADMIN_PASSWORD"):
            print("Set ADMIN_PASSWORD (and optionally ADMIN_USER) to protect the admin panel.", file=sys.stderr)
            return 2
        uvicorn.run(create_app(with_bot=not a.no_bot), host=a.host, port=a.port, log_level="info")
        return 0
    if a.cmd == "akhbar":
        from . import akhbar, control

        cfg = control.load()
        control.apply(cfg)
        if a.edition:
            ed = cfg.edition(a.edition)
            if ed is None:
                ap.error(f"unknown edition {a.edition}; known: {', '.join(e.id for e in cfg.editions)}")
        elif a.region:
            region = next((r for r in akhbar.regions() if r.lower() == a.region.lower()), None)
            if region is None:
                ap.error(f"unknown region {a.region}; use 'central' or one of: {', '.join(akhbar.regions()[1:])}")
            ed = next((e for e in cfg.editions if e.region == region and e.language == a.language), None) or control.NewsEdition(region=region, language=a.language)
        else:
            ap.error("give --edition or --region")
        chans = []
        if a.chat:
            chans = [control.Channel(name="cli", platform="telegram", chat_id=a.chat)]
        elif a.publish:
            chans = control.channels_for_edition(cfg, ed)
        res = akhbar.run(ed.desk(), channels=chans, from_json=a.from_json)
        print("\n" + res.summary())
        return 0 if res.ok else 1
    if a.cmd in ("db-check", "db-init"):
        from . import supabase_db

        if not supabase_db.configured():
            from .config import settings

            print(f"Supabase is not configured (SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY empty).\n"
                  f"Using local SQLite at {settings.db_path} and settings file output/control.json.")
            return 0 if a.cmd == "db-check" else 1
        if a.cmd == "db-init":
            try:
                supabase_db.init_schema()
            except supabase_db.SupabaseError as e:
                print(f"db-init failed: {e}")
                return 1
            print("Schema applied.")
        status = supabase_db.Supabase().check()
        for table, st in status.items():
            print(f"  {table:11} {'OK' if st == 'ok' else 'ERROR: ' + st}")
        ok = all(v == "ok" for v in status.values())
        print("Supabase is ready." if ok else "Fix the errors above (usually: run supabase/schema.sql in the SQL editor).")
        return 0 if ok else 1
    if a.cmd == "bot":
        from .bot import main as bot_main

        bot_main()
        return 0

    from . import pipeline

    platforms = set(pipeline.PLATFORMS) if a.publish == "all" else {p.strip() for p in a.publish.split(",") if p.strip()}
    unknown = platforms - set(pipeline.PLATFORMS)
    if unknown:
        ap.error(f"unknown platform(s): {', '.join(unknown)}")
    result = pipeline.run(
        exam_id=a.exam, content_type=a.type, subject=a.subject, platforms=platforms, chat_id=a.chat,
        from_json=a.from_json, make_video=not a.no_video, seed=a.seed, video_style=a.video_style,
    )
    print("\n" + result.summary())
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
