"""Runtime configuration edited from the admin UI.

Stored as JSON (CONTROL_FILE, default <output>/control.json). On first start it is
seeded from the environment / .env so existing setups keep working. Secrets live in
the same file; the UI only ever shows them masked.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .config import Exam, PACKAGE_DIR, load_exams as load_catalogue, settings
from .models import CONTENT_TYPES, VIDEO_STYLES

Language = Literal["hi", "en", "hinglish"]
Platform = Literal["telegram", "youtube", "instagram"]
OUTPUTS = ("card", "questions", "pdf", "short")  # what a content run produces / posts
SECRET_FIELDS = {"anthropic_api_key", "openai_api_key", "telegram_bot_token", "elevenlabs_api_key", "client_secret", "refresh_token", "access_token"}


def _id() -> str:
    return uuid.uuid4().hex[:8]


class Channel(BaseModel):
    id: str = Field(default_factory=_id)
    name: str
    platform: Platform
    enabled: bool = True
    # telegram
    chat_id: str = ""
    send_video: bool = True
    # youtube
    client_id: str = ""
    client_secret: str = ""
    refresh_token: str = ""
    privacy: Literal["public", "unlisted", "private"] = "public"
    # instagram
    user_id: str = ""
    access_token: str = ""

    def configured(self) -> bool:
        if self.platform == "telegram":
            return bool(self.chat_id)
        if self.platform == "youtube":
            return bool(self.client_id and self.client_secret and self.refresh_token)
        return bool(self.user_id and self.access_token)


class ExamSetting(BaseModel):
    exam_id: str
    enabled: bool = True
    language: Language | None = None  # None = exam's own default
    weight: float = 1.0
    channel_ids: list[str] = Field(default_factory=list)  # empty = all enabled channels


class CustomExam(BaseModel):
    id: str
    name: str
    short_name: str
    language: Language = "hi"
    audience: str = ""
    subjects: dict[str, list[str]] = Field(default_factory=dict)


class NewsEdition(BaseModel):
    """A daily newspaper edition: one region (a state/UT or 'central') in one language."""

    id: str = Field(default_factory=_id)
    region: str = "central"  # 'central' or a state/UT name as in akhbar.STATE_HI
    language: Language = "hi"
    enabled: bool = True
    channel_ids: list[str] = Field(default_factory=list)  # empty = all enabled Telegram channels

    def desk(self):
        from .akhbar import Desk

        return Desk(region=self.region, language=self.language)


def default_editions() -> list[NewsEdition]:
    return [
        NewsEdition(id="rajasthan-hi", region="Rajasthan", language="hi"),
        NewsEdition(id="central-hi", region="central", language="hi"),
        NewsEdition(id="central-en", region="central", language="en", enabled=False),
    ]


class Outputs(BaseModel):
    card: bool = True
    questions: bool = True
    pdf: bool = True
    short: bool = True

    def as_set(self) -> set[str]:
        return {k for k in OUTPUTS if getattr(self, k)}


class Schedule(BaseModel):
    id: str = Field(default_factory=_id)
    name: str
    enabled: bool = True
    kind: Literal["content", "akhbar"] = "content"
    mode: Literal["interval", "daily"] = "interval"
    every_hours: float = 6
    start_at: str = "07:00"  # anchor for interval schedules
    times: list[str] = Field(default_factory=lambda: ["08:00"])  # daily schedules
    days: list[Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]] = Field(default_factory=lambda: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"])
    exam_ids: list[str] = Field(default_factory=list)  # content schedules; empty = all enabled exams
    edition_ids: list[str] = Field(default_factory=list)  # newspaper schedules; empty = all enabled editions
    content_types: list[str] = Field(default_factory=list)  # empty = auto
    video_styles: list[str] = Field(default_factory=list)  # empty = auto
    outputs: Outputs = Field(default_factory=Outputs)
    channel_ids: list[str] = Field(default_factory=list)  # empty = the exam's channels
    language: Language | None = None  # overrides the exam language


class General(BaseModel):
    llm_provider: Literal["anthropic", "openai"] = "anthropic"
    claude_model: str = "claude-opus-5-5"
    openai_model: str = "gpt-5.5"
    brand_name: str = "Indira Pathshala"
    brand_handle: str = "@indirapathshala"
    copyright_owner: str = ""
    timezone: str = "Asia/Kolkata"
    default_language: Language = "hi"
    tts_provider: Literal["auto", "elevenlabs", "edge", "none"] = "auto"
    mnemonic_review: bool = True
    pdf_watermark: bool = True
    akhbar_title_hi: str = "इंदिरा पाठशाला समसामयिकी पत्रिका"
    akhbar_title_en: str = "Indira Pathshala Samsamayiki Patrika"
    akhbar_articles: int = 10
    telegram_admin_ids: list[str] = Field(default_factory=list)


class Secrets(BaseModel):
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    telegram_bot_token: str = ""
    elevenlabs_api_key: str = ""


class ControlConfig(BaseModel):
    general: General = Field(default_factory=General)
    secrets: Secrets = Field(default_factory=Secrets)
    channels: list[Channel] = Field(default_factory=list)
    exams: list[ExamSetting] = Field(default_factory=list)
    custom_exams: list[CustomExam] = Field(default_factory=list)
    schedules: list[Schedule] = Field(default_factory=list)
    editions: list[NewsEdition] = Field(default_factory=default_editions)

    def edition(self, eid: str) -> NewsEdition | None:
        return next((e for e in self.editions if e.id == eid), None)

    def channel(self, cid: str) -> Channel | None:
        return next((c for c in self.channels if c.id == cid), None)

    def schedule(self, sid: str) -> Schedule | None:
        return next((s for s in self.schedules if s.id == sid), None)


# ---------------------------------------------------------------- storage

_lock = threading.RLock()


def control_path() -> Path:
    return Path(os.getenv("CONTROL_FILE") or (settings.output_dir / "control.json"))


def _seed_from_env() -> ControlConfig:
    cfg = ControlConfig()
    g = cfg.general
    g.llm_provider = "openai" if settings.llm_provider == "openai" else "anthropic"
    g.claude_model, g.openai_model = settings.claude_model, settings.openai_model
    g.brand_name, g.brand_handle = settings.brand_name, settings.brand_handle
    g.copyright_owner, g.timezone = settings.copyright_owner, settings.post_timezone
    g.tts_provider, g.mnemonic_review, g.pdf_watermark = settings.tts_provider, settings.mnemonic_review, settings.pdf_watermark  # type: ignore[assignment]
    g.telegram_admin_ids = list(settings.telegram_admin_ids)
    cfg.secrets = Secrets(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        telegram_bot_token=settings.telegram_bot_token,
        elevenlabs_api_key=settings.elevenlabs_api_key,
    )
    if settings.telegram_channel_id:
        cfg.channels.append(Channel(name="Main Telegram channel", platform="telegram", chat_id=settings.telegram_channel_id, send_video=settings.telegram_send_video))
    if settings.youtube_enabled:
        cfg.channels.append(Channel(name="YouTube", platform="youtube", client_id=settings.youtube_client_id,
                                    client_secret=settings.youtube_client_secret, refresh_token=settings.youtube_refresh_token,
                                    privacy=settings.youtube_privacy))  # type: ignore[arg-type]
    if settings.instagram_enabled:
        cfg.channels.append(Channel(name="Instagram", platform="instagram", user_id=settings.instagram_user_id, access_token=settings.instagram_access_token))
    cfg.exams = [ExamSetting(exam_id=e.id, weight=e.weight) for e in load_catalogue().values()]
    cfg.schedules = [
        Schedule(name="Daily posts", mode="daily", times=list(settings.post_times)),
        Schedule(name="Daily current affairs (Akhbar)", kind="akhbar", mode="daily", times=["07:00"], enabled=False),
    ]
    return cfg


def load() -> ControlConfig:
    with _lock:
        p = control_path()
        if p.exists():
            cfg = ControlConfig.model_validate_json(p.read_text(encoding="utf-8"))
        else:
            cfg = _seed_from_env()
            save(cfg)
        # renamed paper: replace the old default titles (custom titles are kept)
        if cfg.general.akhbar_title_hi == "पाठशाला समाचार":
            cfg.general.akhbar_title_hi = General().akhbar_title_hi
        if cfg.general.akhbar_title_en == "Pathshala Times":
            cfg.general.akhbar_title_en = General().akhbar_title_en
        # exams added to the catalogue later show up automatically
        known = {e.exam_id for e in cfg.exams}
        for e in all_exam_defs(cfg).values():
            if e.id not in known:
                cfg.exams.append(ExamSetting(exam_id=e.id, weight=e.weight, enabled=False))
        return cfg


def save(cfg: ControlConfig) -> None:
    with _lock:
        p = control_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(cfg.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(p)
        try:
            os.chmod(p, 0o600)  # contains secrets
        except OSError:
            pass
    apply(cfg)


def apply(cfg: ControlConfig) -> None:
    """Push UI settings into the process-wide settings used by the pipeline."""
    g, s = cfg.general, cfg.secrets
    for k, v in {
        "brand_name": g.brand_name, "brand_handle": g.brand_handle, "copyright_owner": g.copyright_owner,
        "post_timezone": g.timezone, "tts_provider": g.tts_provider, "mnemonic_review": g.mnemonic_review,
        "pdf_watermark": g.pdf_watermark, "telegram_admin_ids": list(g.telegram_admin_ids),
        "llm_provider": g.llm_provider, "claude_model": g.claude_model.strip() or settings.claude_model,
        "openai_model": g.openai_model.strip() or settings.openai_model,
        "akhbar_title_hi": g.akhbar_title_hi, "akhbar_title_en": g.akhbar_title_en, "akhbar_articles": g.akhbar_articles,
    }.items():
        object.__setattr__(settings, k, v)
    if s.telegram_bot_token:
        object.__setattr__(settings, "telegram_bot_token", s.telegram_bot_token)
    if s.elevenlabs_api_key:
        object.__setattr__(settings, "elevenlabs_api_key", s.elevenlabs_api_key)
    from . import providers

    changed = False
    for key, env in (("anthropic_api_key", "ANTHROPIC_API_KEY"), ("openai_api_key", "OPENAI_API_KEY")):
        val = getattr(s, key)
        if val and os.environ.get(env) != val:
            os.environ[env] = val
            changed = True
    if changed:
        providers.reset()  # rebuild clients with the new key
    # switching provider or model needs no rebuild: providers read settings on every call


# ---------------------------------------------------------------- exams & channels

def all_exam_defs(cfg: ControlConfig) -> dict[str, Exam]:
    exams = dict(load_catalogue())
    for c in cfg.custom_exams:
        exams[c.id] = Exam(id=c.id, name=c.name, short_name=c.short_name, language=c.language, weight=1.0,
                           audience=c.audience, subjects=c.subjects)
    return exams


def effective_exams(cfg: ControlConfig, language: str | None = None) -> dict[str, Exam]:
    """Enabled exams with UI overrides (language, weight) applied."""
    defs = all_exam_defs(cfg)
    out = {}
    for es in cfg.exams:
        e = defs.get(es.exam_id)
        if e is None or not es.enabled:
            continue
        out[e.id] = replace(e, language=language or es.language or e.language, weight=es.weight)
    return out


def editions_for(cfg: ControlConfig, schedule: Schedule) -> list[NewsEdition]:
    eds = [e for e in cfg.editions if e.enabled]
    return [e for e in eds if e.id in schedule.edition_ids] if schedule.edition_ids else eds


def channels_for_edition(cfg: ControlConfig, edition: NewsEdition, schedule: Schedule | None = None) -> list[Channel]:
    """Telegram channels for a newspaper: schedule override > the edition's own channels > all."""
    ids = (schedule.channel_ids if schedule and schedule.channel_ids else None) or edition.channel_ids or None
    chans = [c for c in cfg.channels if c.enabled and c.configured() and c.platform == "telegram"]
    return [c for c in chans if c.id in ids] if ids else chans


def channels_for(cfg: ControlConfig, exam_id: str, schedule: Schedule | None = None) -> list[Channel]:
    ids = (schedule.channel_ids if schedule and schedule.channel_ids else None) or next(
        (es.channel_ids for es in cfg.exams if es.exam_id == exam_id and es.channel_ids), None)
    chans = [c for c in cfg.channels if c.enabled and c.configured()]
    return [c for c in chans if c.id in ids] if ids else chans


# ---------------------------------------------------------------- UI helpers

def mask(value: str) -> str:
    return "" if not value else ("•" * 6 + value[-4:] if len(value) > 8 else "•" * 6)


def public_view(cfg: ControlConfig) -> dict:
    """Config for the browser: secrets replaced by masks."""
    data = cfg.model_dump()
    data["secrets"] = {k: mask(v) for k, v in data["secrets"].items()}
    for ch in data["channels"]:
        for f in SECRET_FIELDS & set(ch):
            ch[f] = mask(ch[f])
    return data


def merge_from_ui(cfg: ControlConfig, incoming: dict) -> ControlConfig:
    """Accept the edited config; masked / empty secret fields keep their stored value."""
    def keep(new: str, old: str) -> str:
        return old if (not new or new.startswith("•")) else new

    old_secrets = cfg.secrets.model_dump()
    sec = incoming.get("secrets", {})
    incoming["secrets"] = {k: keep(sec.get(k, ""), old_secrets.get(k, "")) for k in old_secrets}
    old_channels = {c.id: c for c in cfg.channels}
    for ch in incoming.get("channels", []):
        prev = old_channels.get(ch.get("id"))
        for f in SECRET_FIELDS & set(ch):
            ch[f] = keep(ch.get(f, ""), getattr(prev, f, "") if prev else "")
    new = ControlConfig.model_validate(incoming)
    _normalize_edition_ids(new)
    _validate(new)
    return new


def _normalize_edition_ids(cfg: ControlConfig) -> None:
    """Edition ids always read '<region>-<language>' (e.g. uttar-pradesh-hi); schedules follow renames."""
    from .akhbar import Desk

    remap = {}
    for ed in cfg.editions:
        new_id = Desk(ed.region, ed.language).key
        remap[ed.id] = new_id
        ed.id = new_id
    for s in cfg.schedules:
        s.edition_ids = list(dict.fromkeys(remap.get(x, x) for x in s.edition_ids))


def _validate(cfg: ControlConfig) -> None:
    import re
    from zoneinfo import ZoneInfo

    ZoneInfo(cfg.general.timezone)  # raises for an unknown zone
    hhmm = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
    chan_ids = {c.id for c in cfg.channels}
    exam_ids = set(all_exam_defs(cfg))
    for s in cfg.schedules:
        if s.mode == "interval" and not (0.25 <= s.every_hours <= 168):
            raise ValueError(f"Schedule '{s.name}': every_hours must be between 0.25 and 168")
        for t in ([s.start_at] if s.mode == "interval" else s.times):
            if not hhmm.match(t):
                raise ValueError(f"Schedule '{s.name}': time '{t}' must be HH:MM (24h)")
        if s.mode == "daily" and not s.times:
            raise ValueError(f"Schedule '{s.name}': add at least one time")
        bad = [x for x in s.content_types if x not in CONTENT_TYPES] + [x for x in s.video_styles if x not in VIDEO_STYLES]
        bad += [x for x in s.channel_ids if x not in chan_ids] + [x for x in s.exam_ids if x not in exam_ids]
        bad += [x for x in s.edition_ids if x not in {e.id for e in cfg.editions}]
        if bad:
            raise ValueError(f"Schedule '{s.name}': unknown values {bad}")
    from .akhbar import regions

    known_regions = set(regions())
    seen = set()
    for ed in cfg.editions:
        if ed.region not in known_regions:
            raise ValueError(f"Newspaper edition: unknown region '{ed.region}'")
        if (ed.region, ed.language) in seen:
            raise ValueError(f"Two newspaper editions for {ed.region} in the same language")
        seen.add((ed.region, ed.language))
        if [x for x in ed.channel_ids if x not in chan_ids]:
            raise ValueError(f"Newspaper edition {ed.region}: unknown channel")
    for c in cfg.custom_exams:
        if not re.match(r"^[a-z0-9-]{2,40}$", c.id):
            raise ValueError(f"Custom exam id '{c.id}' must be lowercase letters, digits and hyphens")
        if not c.subjects:
            raise ValueError(f"Custom exam '{c.name}' needs at least one subject")


def catalogue_info() -> dict:
    return {
        "content_types": CONTENT_TYPES,
        "video_styles": VIDEO_STYLES,
        "outputs": list(OUTPUTS),
        "languages": {"hi": "हिन्दी", "en": "English", "hinglish": "Hinglish"},
        "regions": _region_choices(),
        "exams_yaml": str(PACKAGE_DIR / "exams.yaml"),
    }


def _region_choices() -> list[dict]:
    from .akhbar import CENTRAL, STATE_HI

    return [{"id": CENTRAL, "label": "Central / National (राष्ट्रीय)"}] + [
        {"id": r, "label": f"{r} ({hi})"} for r, hi in sorted(STATE_HI.items())
    ]
