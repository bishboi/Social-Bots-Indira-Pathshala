"""End-to-end pipeline: pick -> research (web) -> generate -> render -> publish."""

from __future__ import annotations

import json
import logging
import random
import re
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import Exam, load_exams, settings
from .models import CONTENT_TYPES, ContentPackage
from .store import History

log = logging.getLogger(__name__)

PLATFORMS = ("telegram", "youtube", "instagram")

# How often each format is picked on a random run.
TYPE_WEIGHTS = {
    "pyq_quiz": 3,
    "concept_explainer": 2,
    "cheatsheet": 2,
    "formula_sheet": 1,
    "mnemonic_trick": 1,
    "current_affairs": 1,
}
# Formats that don't fit an exam's subjects get skipped.
FORMULA_SUBJECTS = re.compile(r"physics|chem|math|quant|reasoning|भौतिक|रसायन", re.I)
CURRENT_AFFAIRS_EXAMS = {"rajasthan-si", "ssc-cgl", "reet"}


@dataclass
class RunResult:
    run_dir: Path
    exam: Exam
    package: ContentPackage | None = None
    assets: dict[str, Path] = field(default_factory=dict)
    published: dict = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.package is not None and not any(k in self.errors for k in ("research", "generate"))

    def summary(self) -> str:
        lines = [f"Run: {self.run_dir}", f"Exam: {self.exam.name}"]
        if self.package:
            lines += [f"Topic: {self.package.topic} ({self.package.content_type})", f"MCQs: {len(self.package.mcqs)}"]
        lines += [f"Asset {k}: {v}" for k, v in self.assets.items()]
        for k, v in self.published.items():
            url = v.get("url") if isinstance(v, dict) else None
            lines.append(f"Published {k}: {url or 'ok'}")
        lines += [f"ERROR {k}: {v.splitlines()[0][:300]}" for k, v in self.errors.items()]
        return "\n".join(lines)


def _slug(text: str) -> str:
    s = re.sub(r"[^\w]+", "-", text.lower(), flags=re.UNICODE).strip("-")
    return s[:40] or "topic"


def pick_exam(exams: dict[str, Exam], rng: random.Random) -> Exam:
    pool = list(exams.values())
    return rng.choices(pool, weights=[e.weight for e in pool])[0]


def pick_type(exam: Exam, subject: str, rng: random.Random) -> str:
    weights = dict(TYPE_WEIGHTS)
    if not FORMULA_SUBJECTS.search(subject):
        weights.pop("formula_sheet")
    if exam.id not in CURRENT_AFFAIRS_EXAMS and "current" not in subject.lower():
        weights.pop("current_affairs")
    if "current" in subject.lower():
        return "current_affairs"
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def render_all(pkg: ContentPackage, exam: Exam, run_dir: Path, result: RunResult, make_video: bool = True) -> None:
    from .render import images, pdf, video

    steps = [
        ("card", lambda: images.render_card(pkg, exam, run_dir / "card.png")),
        ("pdf", lambda: pdf.render_pdf(pkg, exam, run_dir / "notes.pdf")),
    ]
    for name, fn in steps:
        try:
            result.assets[name] = fn()
        except Exception:
            log.exception("render %s failed", name)
            result.errors[name] = traceback.format_exc()
    if make_video:
        try:
            slides = images.render_slides(pkg, exam, run_dir / "slides")
            result.assets["video"] = video.build_short(pkg, slides, run_dir, exam.language)
        except Exception:
            log.exception("video render failed")
            result.errors["video"] = traceback.format_exc()


def publish_all(pkg: ContentPackage, exam: Exam, result: RunResult, platforms: set[str], chat_id: str | None = None) -> None:
    from .publish import instagram, telegram, youtube

    # Shorts first, so their links could be referenced later if desired.
    if "youtube" in platforms:
        if not settings.youtube_enabled:
            result.errors["youtube"] = "skipped: YouTube credentials not configured"
        elif "video" not in result.assets:
            result.errors["youtube"] = "skipped: no video"
        else:
            try:
                result.published["youtube"] = youtube.publish(pkg, exam, result.assets["video"])
            except Exception:
                log.exception("youtube publish failed")
                result.errors["youtube"] = traceback.format_exc()
    if "instagram" in platforms:
        if not settings.instagram_enabled:
            result.errors["instagram"] = "skipped: Instagram credentials not configured"
        elif "video" not in result.assets:
            result.errors["instagram"] = "skipped: no video"
        else:
            try:
                result.published["instagram"] = instagram.publish(pkg, exam, result.assets["video"], result.assets.get("card"))
            except Exception:
                log.exception("instagram publish failed")
                result.errors["instagram"] = traceback.format_exc()
    if "telegram" in platforms:
        if not settings.telegram_bot_token:
            result.errors["telegram"] = "skipped: TELEGRAM_BOT_TOKEN not set"
        else:
            try:
                result.published["telegram"] = telegram.publish(pkg, exam, result.assets, chat_id=chat_id)
            except Exception:
                log.exception("telegram publish failed")
                result.errors["telegram"] = traceback.format_exc()


def run(
    exam_id: str | None = None,
    content_type: str | None = None,
    subject: str | None = None,
    platforms: set[str] | None = None,
    chat_id: str | None = None,
    from_json: Path | None = None,
    make_video: bool = True,
    seed: int | None = None,
) -> RunResult:
    """Run the full pipeline once. `platforms=None/empty` is a dry run (render only)."""
    from . import llm

    rng = random.Random(seed)
    exams = load_exams()
    history = History(settings.db_path)
    platforms = set(platforms or ())

    if from_json:
        pkg = ContentPackage.model_validate_json(Path(from_json).read_text(encoding="utf-8"))
        exam = exams[exam_id] if exam_id else next((e for e in exams.values() if pkg.subject in e.subjects), pick_exam(exams, rng))
        subject, content_type = pkg.subject, pkg.content_type
    else:
        if exam_id and exam_id not in exams:
            raise KeyError(f"Unknown exam '{exam_id}'. Known: {', '.join(exams)}")
        exam = exams[exam_id] if exam_id else pick_exam(exams, rng)
        subject, seed_topic = exam.random_seed(rng, subject)
        if content_type and content_type not in CONTENT_TYPES:
            raise KeyError(f"Unknown content type '{content_type}'. Known: {', '.join(CONTENT_TYPES)}")
        content_type = content_type or pick_type(exam, subject, rng)
        pkg = None

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = settings.output_dir / f"{stamp}_{exam.id}_{content_type}"
    run_dir.mkdir(parents=True, exist_ok=True)
    result = RunResult(run_dir=run_dir, exam=exam)
    log.info("run %s: exam=%s subject=%s type=%s", run_dir.name, exam.id, subject, content_type)

    if pkg is None:
        try:
            research = llm.research_topic(exam, subject, seed_topic, content_type, history.recent_topics(exam.id))
            (run_dir / "research.md").write_text(research.notes + "\n\nSOURCES\n" + "\n".join(f"- {s['title']}: {s['url']}" for s in research.sources), encoding="utf-8")
        except Exception:
            log.exception("research failed")
            result.errors["research"] = traceback.format_exc()
            _finish(result, history, subject, None, content_type)
            return result
        try:
            pkg = llm.generate_package(exam, subject, content_type, research)
        except Exception:
            log.exception("generation failed")
            result.errors["generate"] = traceback.format_exc()
            _finish(result, history, subject, None, content_type)
            return result

    result.package = pkg
    (run_dir / "content.json").write_text(pkg.model_dump_json(indent=2), encoding="utf-8")
    render_all(pkg, exam, run_dir, result, make_video=make_video)
    if platforms:
        publish_all(pkg, exam, result, platforms, chat_id=chat_id)
    _finish(result, history, subject, pkg.topic, content_type)
    return result


def _finish(result: RunResult, history: History, subject: str, topic: str | None, content_type: str) -> None:
    manifest = {
        "exam": result.exam.id,
        "subject": subject,
        "topic": topic,
        "content_type": content_type,
        "assets": {k: str(v) for k, v in result.assets.items()},
        "published": result.published,
        "errors": result.errors,
    }
    (result.run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    status = "ok" if result.ok and not [k for k in result.errors if not result.errors[k].startswith("skipped")] else ("partial" if result.ok else "failed")
    history.record(result.exam.id, subject, topic or "", content_type, str(result.run_dir), status, result.published)
