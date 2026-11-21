"""CLI.

  python -m pathshala_bot run --space rajasthan-si [--dry-run]   # one run of a space's agent
  python -m pathshala_bot run [--exam rajasthan-si] [--type pyq_quiz] [--publish telegram,youtube,instagram]
  python -m pathshala_bot run --from-json output/<run>/content.json --publish telegram   # re-render / re-post
  python -m pathshala_bot serve        # admin UI + scheduler + Telegram bot (set ADMIN_PASSWORD)
  python -m pathshala_bot akhbar --region Rajasthan --language hi [--publish]   # daily current-affairs paper
  python -m pathshala_bot db-setup     # one-step Supabase setup (check key, create tables, seed settings)
  python -m pathshala_bot db-check     # check the Supabase database (or local SQLite)
  python -m pathshala_bot bot          # bot only, legacy .env schedule
  python -m pathshala_bot exams        # list exams
  python -m pathshala_bot spaces       # list spaces
  python -m pathshala_bot exam-lookup "Rajasthan Patwari" [--save]   # research an exam by name
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
    r.add_argument("--space", help="run this space's agent (id or name from the admin panel): picks a fresh topic, posts to the space's channels unless --dry-run")
    r.add_argument("--dry-run", action="store_true", help="with --space: generate everything but post nothing")
    r.add_argument("--exam", help="exam id from exams.yaml (default: random); with --space: limit the agent to this exam")
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
    sub.add_parser("db-setup", help="one-step Supabase setup: check key + URL, create tables (if SUPABASE_DB_URL), seed settings from .env")

    sub.add_parser("bot", help="Telegram bot only, with the legacy POST_TIMES schedule")
    sub.add_parser("exams", help="list configured exams")
    sub.add_parser("spaces", help="list spaces from the admin panel")
    el = sub.add_parser("exam-lookup", help="find an exam by name; unknown exams are researched on the web (prints the profile)")
    el.add_argument("name", help="exam name as you'd type it, e.g. 'Rajasthan Patwari'")
    el.add_argument("--save", action="store_true", help="add a researched exam to the panel's custom exams")

    a = ap.parse_args(argv)
    if a.cmd == "exams":
        for e in load_exams().values():
            print(f"{e.id:16} {e.language:9} {e.name}  [{', '.join(e.subjects)}]")
        return 0
    if a.cmd == "exam-lookup":
        import json as _json

        from . import control, exam_research

        cfg = control.load()
        control.apply(cfg)
        res = exam_research.lookup(cfg, a.name)
        if "match" in res:
            print(f"Already in the catalogue: {res['match']} ({res['name']})")
            return 0
        print(_json.dumps(res["exam"], ensure_ascii=False, indent=2))
        if a.save:
            cfg.custom_exams.append(control.CustomExam.model_validate(res["exam"]))
            control.save(cfg)
            print(f"Saved as custom exam '{res['exam']['id']}'. Add it to a space in the panel.")
        return 0
    if a.cmd == "spaces":
        from . import control

        cfg = control.load()
        for sp in cfg.spaces:
            when = "manual only" if not sp.schedule.enabled else (f"every {sp.schedule.every_hours}h from {sp.schedule.start_at}"
                                                                   if sp.schedule.mode == "interval" else "daily at " + ", ".join(sp.schedule.times))
            print(f"{sp.id:16} {'on ' if sp.enabled else 'off'} {sp.name}: exams={','.join(sp.exam_ids)} "
                  f"channels={len(control.space_channels(cfg, sp))} ({when})")
        return 0
    if a.cmd == "serve":
        import os

        import uvicorn

        from . import control
        from .web.app import create_app

        # Never exit on setup problems: start the panel and show them there (and in the logs).
        try:
            control.apply(control.load())
        except Exception as e:  # e.g. Supabase tables missing / wrong key - shown on the dashboard
            logging.getLogger("pathshala").error("could not load settings at startup: %s", e)
        if not os.getenv("ADMIN_PASSWORD"):
            logging.getLogger("pathshala").error(
                "ADMIN_PASSWORD is not set - the panel will refuse logins until you add it to .env and restart.")
        shown = "localhost" if a.host in ("0.0.0.0", "127.0.0.1") else a.host
        print(f"\n  Admin panel: http://{shown}:{a.port}   (login: {os.getenv('ADMIN_USER', 'admin')} / ADMIN_PASSWORD)\n", flush=True)
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
    if a.cmd == "db-setup":
        return db_setup()
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
        from .config import settings as _s

        kinds = {"secret": "Secret key (sb_secret_...) - OK", "service_role": "legacy service_role key - OK",
                 "publishable": "Publishable key - WRONG KEY", "anon": "anon key - WRONG KEY"}
        print(f"  URL         {supabase_db.normalize_url(_s.supabase_url)}")
        print(f"  Key type    {kinds.get(supabase_db.key_kind(_s.supabase_key), supabase_db.key_kind(_s.supabase_key))}")
        for problem in supabase_db.diagnose():
            print(f"  PROBLEM     {problem}")
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

    if a.space:
        from . import agent, control

        cfg = control.load()
        control.apply(cfg)
        space = control.find_space(cfg, a.space)
        if space is None:
            ap.error(f"unknown space '{a.space}'. Known: {', '.join(s.id for s in cfg.spaces)}")
        result = agent.run_space(cfg, space, dry_run=a.dry_run, exam_id=a.exam, content_type=a.type, video_style=a.video_style)
        print("\n" + result.summary())
        return 0 if result.ok else 1

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


def db_setup() -> int:
    """Guided Supabase setup. Safe to run again at any time."""
    from . import supabase_db
    from .config import PROJECT_DIR, settings

    def step(n, text):
        print(f"\n[{n}/4] {text}")

    if not supabase_db.configured():
        print("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are not set in .env.\n"
              "Add them (Supabase → Project Settings → API Keys: Project URL + a Secret key sb_secret_... or the legacy service_role key),\n"
              "then recreate the container / restart and run this again.")
        return 1

    step(1, "Checking SUPABASE_URL and the key")
    kinds = {"secret": "Secret key (sb_secret_...)", "service_role": "legacy service_role key",
             "publishable": "Publishable key (wrong key)", "anon": "anon key (wrong key)"}
    kind = supabase_db.key_kind(settings.supabase_key)
    print(f"  URL:      {supabase_db.normalize_url(settings.supabase_url)}")
    print(f"  Key type: {kinds.get(kind, kind)}")
    print(f"  Key:      {supabase_db.fingerprint(settings.supabase_key)}   <- compare with the dashboard")
    problems = supabase_db.diagnose()
    for p in problems:
        print(f"  PROBLEM:  {p}")
    if problems:
        print("\nFix the key/URL in .env, recreate the container (docker rm -f pathshala + docker run ...) and run db-setup again.")
        return 1

    step(2, "Creating tables")
    if settings.supabase_db_url:
        try:
            supabase_db.init_schema()
            print("  Applied supabase/schema.sql")
        except supabase_db.SupabaseError as e:
            print(f"  Could not apply the schema automatically: {e}")
    else:
        print("  SUPABASE_DB_URL is not set, so tables can't be created from here. Either:\n"
              "   a) Supabase dashboard → SQL Editor → New query → paste the file below → Run, or\n"
              "   b) add SUPABASE_DB_URL (Project Settings → Database → Connection string → URI) and run db-setup again.\n"
              f"  File: {PROJECT_DIR / 'supabase' / 'schema.sql'}  (also in the repo: supabase/schema.sql)")

    step(3, "Checking tables")
    try:
        status = supabase_db.Supabase().check()
    except supabase_db.SupabaseError as e:
        print(f"  ERROR: {e}")
        return 1
    for table, st in status.items():
        print(f"  {table:11} {'OK' if st == 'ok' else 'ERROR: ' + st}")
    if not all(v == "ok" for v in status.values()):
        if any("Invalid API key" in v or "(401)" in v for v in status.values()):
            ref = supabase_db.project_ref(settings.supabase_url) or "<project>"
            print(f"""
  Supabase rejected the key (tried it both with and without a Bearer header). To fix:
   1. Open https://supabase.com/dashboard/project/{ref}/settings/api-keys
      - this must be the SAME project as the URL above ({ref}).
   2. Under "Secret keys", click the copy button next to the key (don't select the masked text).
      Or create a new secret key there and copy that.
   3. Put it in .env as  SUPABASE_SERVICE_ROLE_KEY=sb_secret_...   (one line, no quotes/spaces).
   4. Recreate the container:  docker rm -f pathshala  then your docker run command, then db-setup again.
  If a fresh Secret key is still rejected, use the legacy service_role key instead:
   same page → "Legacy API Keys" tab → service_role → Reveal → copy (starts with eyJ...).
  You can test a key directly with:
   curl -s -o /dev/null -w "%{{http_code}}\\n" https://{ref}.supabase.co/rest/v1/ -H "apikey: YOUR_KEY"
   (200 = key accepted, 401 = key rejected)""")
        else:
            print("\n  Create the tables (step 2), then run db-setup again.")
        return 1

    step(4, "Seeding admin-panel settings from .env")
    from . import control

    existed = bool(supabase_db.Supabase().select("app_config", {"select": "id", "id": "eq.1"}))
    cfg = control.load()  # creates the row from .env (or imports output/control.json) when missing
    print(f"  {'Already set up - kept your saved settings' if existed else 'Created settings from .env'}:")
    print(f"    AI provider: {cfg.general.llm_provider} | channels: {len(cfg.channels)} | "
          f"spaces: {len(cfg.spaces)} | newspaper schedules: {len(cfg.schedules)} | newspaper editions: {len(cfg.editions)}")
    print("\nSupabase is ready. Open the admin panel (http://localhost:8080) - it should show 'DB: Supabase'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
