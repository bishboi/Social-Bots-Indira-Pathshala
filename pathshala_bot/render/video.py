"""Assemble slides + per-slide voice-over into a 1080x1920 H.264 short with ffmpeg."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

from ..models import ContentPackage
from .tts import TTS

log = logging.getLogger(__name__)

FPS = 30
PAD = 0.35  # seconds of breathing room after each narration clip
MIN_SLIDE = 2.5
MAX_TOTAL = 58.0  # stay under the 60s Shorts limit


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {' '.join(cmd[:6])}...\n{proc.stderr[-1500:]}")


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return float(json.loads(out)["format"]["duration"])


def _segment(image: Path, audio: Path | None, seconds: float, out: Path) -> None:
    # Gentle zoom-in (Ken Burns) keeps the frame alive; scale up first to avoid jitter.
    frames = max(1, int(seconds * FPS))
    vf = (
        f"scale=2160:3840,zoompan=z='min(zoom+0.0006,1.06)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d={frames}:s=1080x1920:fps={FPS},format=yuv420p"
    )
    cmd = ["ffmpeg", "-y", "-loop", "1", "-framerate", str(FPS), "-i", str(image)]
    if audio:
        cmd += ["-i", str(audio)]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
    cmd += [
        "-filter_complex", f"[0:v]{vf}[v];[1:a]aresample=44100,apad[a]",
        "-map", "[v]", "-map", "[a]", "-t", f"{seconds:.2f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ac", "2", str(out),
    ]
    _run(cmd)


def build_short(pkg: ContentPackage, slide_images: list[Path], work_dir: Path, language: str) -> Path:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is not installed")
    audio_dir = work_dir / "audio"
    seg_dir = work_dir / "segments"
    audio_dir.mkdir(parents=True, exist_ok=True)
    seg_dir.mkdir(parents=True, exist_ok=True)

    tts = TTS(language)
    plan: list[tuple[Path, Path | None, float]] = []
    for i, (slide, img) in enumerate(zip(pkg.short.slides, slide_images)):
        clip = tts.synth(slide.narration, audio_dir / f"vo_{i + 1:02d}.mp3")
        if clip:
            secs = duration(clip) + PAD
        else:  # silent fallback: reading-speed estimate
            secs = len(slide.narration.split()) / 2.6 + 0.8
        plan.append((img, clip, max(MIN_SLIDE, secs)))

    total = sum(p[2] for p in plan)
    if total > MAX_TOTAL:
        log.warning("short is %.1fs; trimming slide durations to fit %.0fs", total, MAX_TOTAL)
        scale = MAX_TOTAL / total
        plan = [(img, clip, secs * scale) for img, clip, secs in plan]

    segments = []
    for i, (img, clip, secs) in enumerate(plan):
        seg = seg_dir / f"seg_{i + 1:02d}.mp4"
        _segment(img, clip, secs, seg)
        segments.append(seg)

    concat = seg_dir / "concat.txt"
    concat.write_text("".join(f"file '{s.resolve()}'\n" for s in segments))
    out = work_dir / "short.mp4"
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", "-movflags", "+faststart", str(out)])
    log.info("short built: %s (%.1fs, voice=%s)", out, duration(out), "yes" if any(p[1] for p in plan) else "silent")
    return out
