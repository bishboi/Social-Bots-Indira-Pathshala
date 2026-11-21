"""CLI.

  python -m pathshala_bot run [--exam rajasthan-si] [--type pyq_quiz] [--publish telegram,youtube,instagram]
  python -m pathshala_bot run --from-json output/<run>/content.json --publish telegram   # re-render / re-post
  python -m pathshala_bot serve        # admin UI + scheduler + Telegram bot (set ADMIN_PASSWORD)
  python -m pathshala_bot akhbar --exam rajasthan-si [--publish]   # daily current-affairs paper
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

    ak = sub.add_parser("akhbar", help="build today's current-affairs newspaper for an exam")
    ak.add_argument("--exam", required=True)
    ak.add_argument("--publish", action="store_true", help="post to the exam's Telegram channels from the admin config")
    ak.add_argument("--chat", help="post to this Telegram chat instead")
    ak.add_argument("--from-json", type=Path, help="render an existing akhbar.json (no AI calls)")

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
        exams = control.all_exam_defs(cfg)
        if a.exam not in exams:
            ap.error(f"unknown exam {a.exam}")
        exam = control.effective_exams(cfg).get(a.exam) or exams[a.exam]
        chans = []
        if a.chat:
            chans = [control.Channel(name="cli", platform="telegram", chat_id=a.chat)]
        elif a.publish:
            chans = [c for c in control.channels_for(cfg, a.exam) if c.platform == "telegram"]
        res = akhbar.run(exam, channels=chans, from_json=a.from_json)
        print("\n" + res.summary())
        return 0 if res.ok else 1
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
