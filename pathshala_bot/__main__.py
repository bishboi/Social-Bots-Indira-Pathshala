"""CLI.

  python -m pathshala_bot run [--exam rajasthan-si] [--type pyq_quiz] [--publish telegram,youtube,instagram]
  python -m pathshala_bot run --from-json output/<run>/content.json --publish telegram   # re-render / re-post
  python -m pathshala_bot bot          # long-running Telegram bot with daily schedule
  python -m pathshala_bot exams        # list exams
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import load_exams
from .models import CONTENT_TYPES


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
    r.add_argument("--no-video", action="store_true", help="skip the short video")
    r.add_argument("--seed", type=int, help="random seed for reproducible picks")

    sub.add_parser("bot", help="run the Telegram bot (polling + daily schedule)")
    sub.add_parser("exams", help="list configured exams")

    a = ap.parse_args(argv)
    if a.cmd == "exams":
        for e in load_exams().values():
            print(f"{e.id:16} {e.language:9} w={e.weight:<4} {e.name}  [{', '.join(e.subjects)}]")
        return 0
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
        from_json=a.from_json, make_video=not a.no_video, seed=a.seed,
    )
    print("\n" + result.summary())
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
