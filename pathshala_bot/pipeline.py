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
from .models import CONTENT_TYPES, VIDEO_STYLES, ContentPackage
from .store import get_history

log = logging.getLogger(__name__)


def _last_line(text: str) -> str:
    """The informative line of a traceback (the exception message), not 'Traceback ...'."""
    lines = [l for l in text.strip().splitlines() if l.strip()]
    return (lines[-1] if lines else text)[:300]

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
GEO_SUBJECTS = re.compile(r"geograph|भूगोल|\bgk\b|general awareness|environment|ecology", re.I)
HISTORY_SUBJECTS = re.compile(r"histor|इतिहास|national movement|culture", re.I)


@dataclass
class RunResult:
    run_dir: Path
    exam: Exam
    package: ContentPackage | None = None
    assets: dict[str, Path] = field(default_factory=dict)
    published: dict = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    credits: list[str] = field(default_factory=list)
    video_style: str | None = None
    space_id: str | None = None
    plan_note: str = ""

    @property
    def ok(self) -> bool:
        return self.package is not None and not any(k in self.errors for k in ("research", "generate"))

    def summary(self) -> str:
        lines = [f"Run: {self.run_dir}", f"Exam: {self.exam.name}"]
        if self.space_id:
            lines.insert(1, f"Space: {self.space_id}")
        if self.plan_note:
            lines.append(f"Plan: {self.plan_note}")
        if self.package:
            lines += [f"Topic: {self.package.topic} ({self.package.content_type})", f"MCQs: {len(self.package.mcqs)}"]
            lines.append(f"Video style: {self.video_style or self.package.short.style}")
        lines += [f"Asset {k}: {v}" for k, v in self.assets.items()]
        for k, v in self.published.items():
            url = v.get("url") if isinstance(v, dict) else None
            lines.append(f"Published {k}: {url or 'ok'}")
        lines += [f"ERROR {k}: {_last_line(v)}" for k, v in self.errors.items()]
        return "\n".join(lines)


def _slug(text: str) -> str:
    s = re.sub(r"[^\w]+", "-", text.lower(), flags=re.UNICODE).strip("-")
    return s[:40] or "topic"


def pick_exam(exams: dict[str, Exam], rng: random.Random) -> Exam:
    return rng.choice(list(exams.values()))


def pick_type(exam: Exam, subject: str, rng: random.Random) -> str:
    weights = dict(TYPE_WEIGHTS)
    if not FORMULA_SUBJECTS.search(subject):
        weights.pop("formula_sheet")
    if exam.id not in CURRENT_AFFAIRS_EXAMS and "current" not in subject.lower():
        weights.pop("current_affairs")
    if "current" in subject.lower():
        return "current_affairs"
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def pick_style(exam: Exam, subject: str, content_type: str, rng: random.Random) -> str:
    """Choose a video style that suits the topic, with some variety."""
    geo_subj, hist_subj, formula_subj = bool(GEO_SUBJECTS.search(subject)), bool(HISTORY_SUBJECTS.search(subject)), bool(FORMULA_SUBJECTS.search(subject))
    options: dict[str, float] = {"concept_explainer": 1}
    if content_type == "pyq_quiz":
        options.update(quiz_countdown=4)
    if content_type in ("cheatsheet", "current_affairs", "mnemonic_trick"):
        options.update(top_facts=3)
    if content_type in ("formula_sheet", "concept_explainer") and formula_subj:
        options.update(formula_breakdown=4)
    if geo_subj:
        options.update(map_story=4)
    if hist_subj:
        options.update(timeline=3, map_story=options.get("map_story", 0) + 1.5)
    return rng.choices(list(options), weights=list(options.values()))[0]


def _map_figure(pkg: ContentPackage, exam: Exam, run_dir: Path) -> Path | None:
    from . import geo

    sc = next((s for s in pkg.short.scenes if s.kind == "map" and s.map), None)
    if not sc:
        return None
    try:
        return geo.render_map_png(sc.map, run_dir / "map.png", title=sc.heading, language=exam.language)
    except Exception:
        log.exception("map figure failed")
        return None


def render_all(pkg: ContentPackage, exam: Exam, run_dir: Path, result: RunResult, make_video: bool = True, style: str | None = None,
               make_pdf: bool = True) -> None:
    from .render import images, pdf, video

    map_png = _map_figure(pkg, exam, run_dir)
    if map_png:
        result.assets["map"] = map_png
    steps = [
        ("card", lambda: images.render_card(pkg, exam, run_dir / "card.png")),
        ("pdf", lambda: pdf.render_pdf(pkg, exam, run_dir / "notes.pdf", map_png=map_png)),
    ]
    if not make_pdf:
        steps = steps[:1]
    for name, fn in steps:
        try:
            result.assets[name] = fn()
        except Exception:
            log.exception("render %s failed", name)
            result.errors[name] = traceback.format_exc()
    if make_video:
        try:
            path, credits, used = video.build_short(pkg, exam, run_dir, style=style)
            result.assets["video"] = path
            result.credits += credits
            result.video_style = used
        except Exception:
            log.exception("video render failed")
            result.errors["video"] = traceback.format_exc()


def publish_to_channels(pkg: ContentPackage, exam: Exam, result: RunResult, channels: list, outputs: set[str]) -> None:
    """Publish to channels configured in the admin UI (control.Channel objects)."""
    from .publish import instagram, telegram, youtube

    for ch in channels:
        key = f"{ch.platform}:{ch.name}"
        try:
            if ch.platform == "telegram":
                result.published[key] = telegram.publish(pkg, exam, result.assets, chat_id=ch.chat_id, include=outputs, send_video=ch.send_video)
            elif "short" not in outputs or "video" not in result.assets:
                result.errors[key] = "skipped: shorts are off for this run" if "short" not in outputs else "skipped: no video"
            elif ch.platform == "youtube":
                creds = {"client_id": ch.client_id, "client_secret": ch.client_secret, "refresh_token": ch.refresh_token, "privacy": ch.privacy}
                result.published[key] = youtube.publish(pkg, exam, result.assets["video"], credits=result.credits, creds=creds)
            elif ch.platform == "instagram":
                creds = {"user_id": ch.user_id, "access_token": ch.access_token}
                result.published[key] = instagram.publish(pkg, exam, result.assets["video"], result.assets.get("card"), credits=result.credits, creds=creds)
        except Exception:
            log.exception("publish to %s failed", key)
            result.errors[key] = traceback.format_exc()


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
                result.published["youtube"] = youtube.publish(pkg, exam, result.assets["video"], credits=result.credits)
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
                result.published["instagram"] = instagram.publish(pkg, exam, result.assets["video"], result.assets.get("card"), credits=result.credits)
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
    video_style: str | None = None,
    exams: dict[str, Exam] | None = None,
    outputs: set[str] | None = None,
    channels: list | None = None,
    seed_topic: str | None = None,
    space_id: str | None = None,
    avoid: list[str] | None = None,
    plan_note: str = "",
) -> RunResult:
    """Run the full pipeline once.

    platforms/chat_id: legacy .env-based publishing; channels: admin-UI channels (takes precedence).
    outputs: subset of {"card", "questions", "pdf", "short"}; exams: pool to pick from (UI overrides applied).
    seed_topic/space_id/avoid: set by a space's agent (agent.run_space): the chosen topic, the space the
    run is recorded under, and the space's recent topics that research must not repeat.
    Nothing is published when neither platforms nor channels are given (dry run).
    """
    from . import llm

    rng = random.Random(seed)
    exams = exams or load_exams()
    outputs = set(outputs or ("card", "questions", "pdf", "short"))
    make_video = make_video and "short" in outputs
    history = get_history()
    platforms = set(platforms or ())

    if from_json:
        pkg = ContentPackage.model_validate_json(Path(from_json).read_text(encoding="utf-8"))
        exam = exams[exam_id] if exam_id else next((e for e in exams.values() if pkg.subject in e.subjects), pick_exam(exams, rng))
        subject, content_type = pkg.subject, pkg.content_type
    else:
        if exam_id and exam_id not in exams:
            raise KeyError(f"Unknown exam '{exam_id}'. Known: {', '.join(exams)}")
        exam = exams[exam_id] if exam_id else pick_exam(exams, rng)
        if not (seed_topic and subject in exam.subjects):  # else: chosen by the space's agent
            subject, seed_topic = exam.random_seed(rng, subject)
        if content_type and content_type not in CONTENT_TYPES:
            raise KeyError(f"Unknown content type '{content_type}'. Known: {', '.join(CONTENT_TYPES)}")
        content_type = content_type or pick_type(exam, subject, rng)
        pkg = None
    if video_style and video_style not in VIDEO_STYLES:
        raise KeyError(f"Unknown video style '{video_style}'. Known: {', '.join(VIDEO_STYLES)}")
    if not from_json:
        video_style = video_style or pick_style(exam, subject, content_type, rng)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = settings.output_dir / f"{stamp}_{exam.id}_{content_type}"
    run_dir.mkdir(parents=True, exist_ok=True)
    result = RunResult(run_dir=run_dir, exam=exam, space_id=space_id, plan_note=plan_note)
    log.info("run %s: exam=%s subject=%s type=%s video=%s", run_dir.name, exam.id, subject, content_type, video_style or "from json")

    if pkg is None:
        try:
            recent = avoid if avoid is not None else history.recent_topics(exam.id)
            research = llm.research_topic(exam, subject, seed_topic, content_type, recent)
            (run_dir / "research.md").write_text(research.notes + "\n\nSOURCES\n" + "\n".join(f"- {s['title']}: {s['url']}" for s in research.sources), encoding="utf-8")
        except Exception:
            log.exception("research failed")
            result.errors["research"] = traceback.format_exc()
            _finish(result, history, subject, None, content_type)
            return result
        try:
            pkg = llm.generate_package(exam, subject, content_type, research, video_style)
        except Exception:
            log.exception("generation failed")
            result.errors["generate"] = traceback.format_exc()
            _finish(result, history, subject, None, content_type)
            return result

    result.package = pkg
    (run_dir / "content.json").write_text(pkg.model_dump_json(indent=2), encoding="utf-8")
    render_all(pkg, exam, run_dir, result, make_video=make_video, style=video_style, make_pdf="pdf" in outputs)
    if channels:
        publish_to_channels(pkg, exam, result, channels, outputs)
    elif platforms:
        publish_all(pkg, exam, result, platforms, chat_id=chat_id)
    _finish(result, history, subject, pkg.topic, content_type)
    return result


def _finish(result: RunResult, history, subject: str, topic: str | None, content_type: str) -> None:
    manifest = {
        "exam": result.exam.id,
        "space": result.space_id,
        "plan": result.plan_note,
        "subject": subject,
        "topic": topic,
        "content_type": content_type,
        "video_style": result.video_style,
        "credits": result.credits,
        "assets": {k: str(v) for k, v in result.assets.items()},
        "published": result.published,
        "errors": result.errors,
    }
    (result.run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    status = "ok" if result.ok and not [k for k in result.errors if not result.errors[k].startswith("skipped")] else ("partial" if result.ok else "failed")
    history.record(result.exam.id, subject, topic or "", content_type, str(result.run_dir), status, result.published,
                   content=result.package.model_dump(mode="json") if result.package else None, space_id=result.space_id)
