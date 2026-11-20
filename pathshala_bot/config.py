"""Runtime settings (environment variables / .env) and the exam catalogue."""

from __future__ import annotations

import os
import random
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent

load_dotenv(PROJECT_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _list(name: str) -> list[str]:
    return [x.strip() for x in _env(name).split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    # Branding
    brand_name: str = field(default_factory=lambda: _env("BRAND_NAME", "Indira Pathshala"))
    brand_handle: str = field(default_factory=lambda: _env("BRAND_HANDLE", "@indirapathshala"))

    # Claude
    claude_model: str = field(default_factory=lambda: _env("CLAUDE_MODEL", "claude-opus-5-5"))
    research_effort: str = field(default_factory=lambda: _env("RESEARCH_EFFORT", "medium"))
    generation_effort: str = field(default_factory=lambda: _env("GENERATION_EFFORT", "high"))
    max_web_searches: int = field(default_factory=lambda: int(_env("MAX_WEB_SEARCHES", "6")))

    # Telegram
    telegram_bot_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    telegram_channel_id: str = field(default_factory=lambda: _env("TELEGRAM_CHANNEL_ID"))
    telegram_admin_ids: list[str] = field(default_factory=lambda: _list("TELEGRAM_ADMIN_IDS"))
    telegram_send_video: bool = field(default_factory=lambda: _bool("TELEGRAM_SEND_VIDEO", True))
    # Daily auto-post times for the bot's scheduler, "HH:MM" in POST_TIMEZONE.
    post_times: list[str] = field(default_factory=lambda: _list("POST_TIMES") or ["08:00", "18:00"])
    post_timezone: str = field(default_factory=lambda: _env("POST_TIMEZONE", "Asia/Kolkata"))

    # YouTube (OAuth refresh-token flow, see scripts/youtube_auth.py)
    youtube_client_id: str = field(default_factory=lambda: _env("YOUTUBE_CLIENT_ID"))
    youtube_client_secret: str = field(default_factory=lambda: _env("YOUTUBE_CLIENT_SECRET"))
    youtube_refresh_token: str = field(default_factory=lambda: _env("YOUTUBE_REFRESH_TOKEN"))
    youtube_privacy: str = field(default_factory=lambda: _env("YOUTUBE_PRIVACY", "public"))

    # Instagram (Graph API, Instagram professional account)
    instagram_user_id: str = field(default_factory=lambda: _env("INSTAGRAM_USER_ID"))
    instagram_access_token: str = field(default_factory=lambda: _env("INSTAGRAM_ACCESS_TOKEN"))
    instagram_graph_version: str = field(default_factory=lambda: _env("INSTAGRAM_GRAPH_VERSION", "v21.0"))
    # Optional: if set, reels are published from <base>/<run>/short.mp4 instead of
    # being uploaded directly (use when you sync ./output to a CDN / bucket).
    media_public_base_url: str = field(default_factory=lambda: _env("MEDIA_PUBLIC_BASE_URL"))

    # Text to speech
    tts_provider: str = field(default_factory=lambda: _env("TTS_PROVIDER", "auto"))  # auto|elevenlabs|edge|none
    elevenlabs_api_key: str = field(default_factory=lambda: _env("ELEVENLABS_API_KEY"))
    elevenlabs_voice_id: str = field(default_factory=lambda: _env("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM"))
    elevenlabs_model: str = field(default_factory=lambda: _env("ELEVENLABS_MODEL", "eleven_multilingual_v2"))
    edge_voice_hi: str = field(default_factory=lambda: _env("EDGE_VOICE_HI", "hi-IN-SwaraNeural"))
    edge_voice_en: str = field(default_factory=lambda: _env("EDGE_VOICE_EN", "en-IN-NeerjaNeural"))

    # Storage
    output_dir: Path = field(default_factory=lambda: Path(_env("OUTPUT_DIR", str(PROJECT_DIR / "output"))))
    db_path: Path = field(default_factory=lambda: Path(_env("DB_PATH", str(PROJECT_DIR / "output" / "history.sqlite3"))))
    fonts_dir: Path = field(default_factory=lambda: Path(_env("FONTS_DIR", str(PROJECT_DIR / "assets" / "fonts"))))

    @property
    def youtube_enabled(self) -> bool:
        return bool(self.youtube_client_id and self.youtube_client_secret and self.youtube_refresh_token)

    @property
    def instagram_enabled(self) -> bool:
        return bool(self.instagram_user_id and self.instagram_access_token)

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_channel_id)


@dataclass(frozen=True)
class Exam:
    id: str
    name: str
    short_name: str
    language: str
    weight: float
    audience: str
    subjects: dict[str, list[str]]
    telegram_chat: str | None = None

    def random_seed(self, rng: random.Random, subject: str | None = None) -> tuple[str, str]:
        subj = subject if subject in self.subjects else rng.choice(list(self.subjects))
        return subj, rng.choice(self.subjects[subj])


def load_exams(path: Path | None = None) -> dict[str, Exam]:
    path = path or Path(_env("EXAMS_FILE", str(PACKAGE_DIR / "exams.yaml")))
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    exams: dict[str, Exam] = {}
    for raw in data["exams"]:
        exams[raw["id"]] = Exam(
            id=raw["id"],
            name=raw["name"],
            short_name=raw.get("short_name", raw["name"]),
            language=raw.get("language", "en"),
            weight=float(raw.get("weight", 1)),
            audience=raw.get("audience", ""),
            subjects={k: list(v) for k, v in raw["subjects"].items()},
            telegram_chat=raw.get("telegram_chat"),
        )
    return exams


settings = Settings()
