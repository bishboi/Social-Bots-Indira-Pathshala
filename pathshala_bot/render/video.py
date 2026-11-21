"""Build the vertical short: voice-over per scene, then Manim animation (or static slides).

Styles (models.VIDEO_STYLES): quiz_countdown, map_story, timeline, formula_breakdown,
top_facts, concept_explainer -> rendered with Manim; slideshow -> Pillow slides + ffmpeg.
If Manim fails for any reason the static slideshow is used so a run never loses its video.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from ..config import Exam, settings
from ..models import ContentPackage
from .tts import TTS

log = logging.getLogger(__name__)

FPS = 30
PAD = 0.45  # breathing room after each narration clip
MIN_SCENE = 2.8
SOFT_MAX = 60.0  # Shorts/Reels allow up to 3 min, but < 60s performs best

LABELS_HI = {"bullets": "मुख्य बिंदु", "fact": "रोचक तथ्य", "quiz": "क्विज़ टाइम", "reveal": "सही उत्तर", "timeline": "समयरेखा",
             "map": "मानचित्र पर", "formula": "सूत्र", "comparison": "तुलना", "image": "देखिए"}


def _run(cmd: list[str], **kw) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if proc.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {' '.join(map(str, cmd[1:5]))}...\n{proc.stderr[-2500:]}")


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return float(json.loads(out)["format"]["duration"])


def manim_available() -> bool:
    try:
        import manim  # noqa: F401
        return True
    except Exception:
        return False


def normalize_scenes(pkg: ContentPackage) -> None:
    """Fix common gaps: a reveal without options inherits the previous quiz's options/answer."""
    last_quiz = None
    for sc in pkg.short.scenes:
        if sc.kind == "quiz":
            last_quiz = sc
        elif sc.kind == "reveal" and last_quiz is not None:
            if not sc.options:
                sc.options = list(last_quiz.options)
            if sc.correct_index is None:
                sc.correct_index = last_quiz.correct_index


def plan_audio(pkg: ContentPackage, work_dir: Path, language: str) -> list[dict]:
    audio_dir = work_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    tts = TTS(language)
    plan = []
    for i, sc in enumerate(pkg.short.scenes):
        clip = tts.synth(sc.narration, audio_dir / f"vo_{i + 1:02d}.mp3")
        secs = duration(clip) + PAD if clip else len(sc.narration.split()) / 2.6 + 0.8
        if sc.kind == "quiz":
            secs += 3.0  # thinking time under the countdown
        plan.append({"audio": str(clip) if clip else None, "seconds": max(MIN_SCENE, secs)})
    total = sum(p["seconds"] for p in plan)
    if total > SOFT_MAX:
        log.warning("short is %.1fs (> %.0fs); consider tighter narration", total, SOFT_MAX)
    return plan


def fetch_media(pkg: ContentPackage, work_dir: Path, plan: list[dict]) -> list[str]:
    from .. import resources

    credits = []
    for sc, p in zip(pkg.short.scenes, plan):
        if sc.kind == "image" and sc.image_topic:
            media = resources.fetch_image(sc.image_topic, work_dir / "media")
            if media:
                p["media"] = {"path": str(media.path), "credit": media.credit}
                credits.append(media.credit)
    return credits


def render_manim(pkg: ContentPackage, exam: Exam, plan: list[dict], work_dir: Path) -> Path:
    from . import fonts

    spec = {
        "scenes": [sc.model_dump() for sc in pkg.short.scenes],
        "plan": plan,
        "language": exam.language,
        "exam": exam.short_name,
        "brand": settings.brand_name,
        "handle": settings.brand_handle,
        "labels": LABELS_HI if exam.language == "hi" else {},
        "font_files": sorted({str(fonts.font_path(s, w)) for s in ("deva", "latin") for w in ("regular", "medium", "semibold", "bold", "heavy")}),
        "latex": bool(shutil.which("latex") and shutil.which("dvisvgm")),
        "out_dir": str(work_dir),
        "fps": int(os.getenv("MANIM_FPS", FPS)),
    }
    spec_path = work_dir / "manim_spec.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    _run([sys.executable, "-m", "pathshala_bot.render.manim_scene", str(spec_path)], cwd=str(Path(__file__).resolve().parents[2]))
    out = work_dir / "short.mp4"
    _run(["ffmpeg", "-y", "-i", str(out), "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(work_dir / "short_fs.mp4")])
    (work_dir / "short_fs.mp4").replace(out)
    return out


def _segment(image: Path, audio: str | None, seconds: float, out: Path) -> None:
    frames = max(1, int(seconds * FPS))
    vf = (f"scale=1620:2880,zoompan=z='min(zoom+0.0006,1.06)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
          f":d={frames}:s=1080x1920:fps={FPS},format=yuv420p")
    cmd = ["ffmpeg", "-y", "-loop", "1", "-framerate", str(FPS), "-i", str(image)]
    cmd += ["-i", audio] if audio else ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
    cmd += ["-filter_complex", f"[0:v]{vf}[v];[1:a]aresample=44100,apad[a]", "-map", "[v]", "-map", "[a]",
            "-t", f"{seconds:.2f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-ac", "2", str(out)]
    _run(cmd)


def render_slideshow(pkg: ContentPackage, exam: Exam, plan: list[dict], work_dir: Path) -> Path:
    from . import images

    slides = images.render_slides(pkg, exam, work_dir / "slides")
    seg_dir = work_dir / "segments"
    seg_dir.mkdir(parents=True, exist_ok=True)
    segments = []
    for i, (img, p) in enumerate(zip(slides, plan)):
        seg = seg_dir / f"seg_{i + 1:02d}.mp4"
        _segment(img, p["audio"], p["seconds"], seg)
        segments.append(seg)
    concat = seg_dir / "concat.txt"
    concat.write_text("".join(f"file '{s.resolve()}'\n" for s in segments))
    out = work_dir / "short.mp4"
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", "-movflags", "+faststart", str(out)])
    return out


def build_short(pkg: ContentPackage, exam: Exam, work_dir: Path, style: str | None = None) -> tuple[Path, list[str], str]:
    """Returns (video path, media credits, style actually used)."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is not installed")
    style = style or pkg.short.style or "slideshow"
    normalize_scenes(pkg)
    plan = plan_audio(pkg, work_dir, exam.language)
    credits = fetch_media(pkg, work_dir, plan)
    used = style
    out = None
    if style != "slideshow":
        if manim_available():
            try:
                out = render_manim(pkg, exam, plan, work_dir)
            except Exception as e:
                log.error("Manim render failed, falling back to slideshow: %s", str(e)[-1500:])
        else:
            log.warning("manim is not installed; using slideshow style")
    if out is None:
        used = "slideshow"
        out = render_slideshow(pkg, exam, plan, work_dir)
    log.info("short built: %s (%.1fs, style=%s, voice=%s)", out, duration(out), used, "yes" if any(p["audio"] for p in plan) else "silent")
    return out, credits, used
