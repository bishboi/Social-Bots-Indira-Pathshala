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

Language = str  # any code in languages.LANGUAGES (validated on save)
Platform = Literal["telegram", "youtube", "instagram"]
OUTPUTS = ("card", "questions", "pdf", "short")  # what a content run produces / posts
SECRET_FIELDS = {"anthropic_api_key", "openai_api_key", "gemini_api_key", "openrouter_api_key", "telegram_bot_token", "elevenlabs_api_key", "client_secret", "refresh_token", "access_token"}


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


class CustomExam(BaseModel):
    id: str
    name: str
    short_name: str
    language: Language = "hi"
    audience: str = ""
    subjects: dict[str, list[str]] = Field(default_factory=dict)
    # filled when the exam was added by typing its name (AI web research)
    typed_name: str = ""
    conducting_body: str = ""
    pattern: str = ""
    sources: list[str] = Field(default_factory=list)
    researched_on: str = ""

    def details(self) -> str:
        parts = [f"Conducted by {self.conducting_body}." if self.conducting_body else "", f"Pattern: {self.pattern}" if self.pattern else ""]
        return " ".join(p for p in parts if p)


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


Day = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
ALL_DAYS: list = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class Timing(BaseModel):
    """When something runs: every X hours from a start time, or at set times each day."""

    mode: Literal["interval", "daily"] = "interval"
    every_hours: float = 6
    start_at: str = "07:00"  # anchor for interval schedules
    times: list[str] = Field(default_factory=lambda: ["08:00"])  # daily schedules
    days: list[Day] = Field(default_factory=lambda: list(ALL_DAYS))


class SpaceSchedule(Timing):
    enabled: bool = True


class Space(BaseModel):
    """A group of exams with its own channels, content settings and schedule.

    Each space has its own agent: on every run it looks at what this space already
    posted, picks a different topic from one of its exams, and posts to the space's channels.
    """

    id: str = Field(default_factory=_id)
    name: str
    enabled: bool = True
    exam_ids: list[str] = Field(default_factory=list)
    channel_ids: list[str] = Field(default_factory=list)  # empty = generate only, post nowhere
    language: Language | None = None  # None = each exam's own language
    focus: str = ""  # optional guidance for the agent, e.g. "only Rajasthan GK and current affairs"
    outputs: Outputs = Field(default_factory=Outputs)
    content_types: list[str] = Field(default_factory=list)  # empty = automatic
    video_styles: list[str] = Field(default_factory=list)  # empty = automatic
    memory: int = 40  # how many past topics of this space the agent avoids repeating
    schedule: SpaceSchedule = Field(default_factory=SpaceSchedule)


class TestStage(BaseModel):
    """One step of a test-series funnel."""

    kind: Literal["pyq", "subject", "full"]
    enabled: bool = True
    count: int = 3  # pyq: latest N papers · subject: tests per subject · full: number of mock tests
    questions: int = 0  # subject tests only: questions per test (0 = the subject's share of a full paper)
    access: Literal["free", "signup", "private"] = "free"  # free: posted + open · signup: name+phone to attempt · private: admin only


def default_stages() -> list[TestStage]:
    return [TestStage(kind="pyq", count=3, access="free"),
            TestStage(kind="subject", count=2, questions=25, access="signup"),
            TestStage(kind="full", count=3, access="signup")]


class TestSeries(BaseModel):
    """A full test series for one exam: previous-year papers -> subject-wise tests -> full-length mocks.

    Its agent researches the exam's pattern once (blueprint: sections, questions per subject and
    topic, marking, duration) and builds every test to that ratio. A schedule releases the next
    test of the funnel to the series' channels.
    """

    id: str = Field(default_factory=_id)
    name: str
    exam_id: str
    enabled: bool = True
    language: Language | None = None  # None = the exam's language
    channel_ids: list[str] = Field(default_factory=list)
    stages: list[TestStage] = Field(default_factory=default_stages)
    diagrams: bool = True  # figure questions get SVG diagrams drawn by an OpenRouter model
    focus: str = ""  # optional guidance, e.g. "Paper 1 only", "level slightly above the real exam"
    schedule: SpaceSchedule = Field(default_factory=lambda: SpaceSchedule(enabled=False, mode="daily", times=["10:00"]))

    def stage(self, kind: str) -> TestStage | None:
        return next((s for s in self.stages if s.kind == kind and s.enabled), None)


class Schedule(Timing):
    """Daily newspaper schedule (content runs are scheduled per space)."""

    id: str = Field(default_factory=_id)
    name: str
    enabled: bool = True
    kind: Literal["akhbar"] = "akhbar"
    edition_ids: list[str] = Field(default_factory=list)  # empty = all enabled editions
    outputs: Outputs = Field(default_factory=Outputs)  # only .questions is used (quiz polls)
    channel_ids: list[str] = Field(default_factory=list)  # empty = each edition's channels
    language: Language | None = None  # overrides the edition language


class General(BaseModel):
    llm_provider: Literal["anthropic", "openai"] = "anthropic"
    claude_model: str = "claude-opus-5-5"
    openai_model: str = "gpt-5.5"
    brand_name: str = "Indira Pathshala"
    brand_handle: str = "@indirapathshala"
    copyright_owner: str = ""
    timezone: str = "Asia/Kolkata"
    default_language: Language = "hi"
    tts_provider: Literal["auto", "gemini", "edge", "elevenlabs", "none"] = "auto"
    mnemonic_review: bool = True
    pdf_watermark: bool = True
    akhbar_title_hi: str = "इंदिरा पाठशाला समसामयिकी पत्रिका"
    akhbar_title_en: str = "Indira Pathshala Samsamayiki Patrika"
    akhbar_articles: int = 10
    max_parallel_runs: int = 2  # spaces run independently; at most this many render at the same time
    openrouter_model: str = "anthropic/claude-sonnet-5.5"  # draws SVG figures for test questions
    openrouter_fallback: str = "google/gemini-3.8-flash"
    public_base_url: str = ""  # e.g. https://tests.example.com - links to online tests in posts
    telegram_admin_ids: list[str] = Field(default_factory=list)


class Secrets(BaseModel):
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""
    telegram_bot_token: str = ""
    elevenlabs_api_key: str = ""
    openrouter_api_key: str = ""


class ControlConfig(BaseModel):
    general: General = Field(default_factory=General)
    secrets: Secrets = Field(default_factory=Secrets)
    channels: list[Channel] = Field(default_factory=list)
    spaces: list[Space] = Field(default_factory=list)
    custom_exams: list[CustomExam] = Field(default_factory=list)
    schedules: list[Schedule] = Field(default_factory=list)  # newspaper schedules
    test_series: list[TestSeries] = Field(default_factory=list)
    editions: list[NewsEdition] = Field(default_factory=default_editions)

    def space(self, sid: str) -> Space | None:
        return next((s for s in self.spaces if s.id == sid), None)

    def edition(self, eid: str) -> NewsEdition | None:
        return next((e for e in self.editions if e.id == eid), None)

    def channel(self, cid: str) -> Channel | None:
        return next((c for c in self.channels if c.id == cid), None)

    def schedule(self, sid: str) -> Schedule | None:
        return next((s for s in self.schedules if s.id == sid), None)

    def series(self, sid: str) -> TestSeries | None:
        return next((s for s in self.test_series if s.id == sid), None)


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
        gemini_api_key=settings.gemini_api_key,
        telegram_bot_token=settings.telegram_bot_token,
        elevenlabs_api_key=settings.elevenlabs_api_key,
        openrouter_api_key=settings.openrouter_api_key,
    )
    if settings.telegram_channel_id:
        cfg.channels.append(Channel(name="Main Telegram channel", platform="telegram", chat_id=settings.telegram_channel_id, send_video=settings.telegram_send_video))
    if settings.youtube_enabled:
        cfg.channels.append(Channel(name="YouTube", platform="youtube", client_id=settings.youtube_client_id,
                                    client_secret=settings.youtube_client_secret, refresh_token=settings.youtube_refresh_token,
                                    privacy=settings.youtube_privacy))  # type: ignore[arg-type]
    if settings.instagram_enabled:
        cfg.channels.append(Channel(name="Instagram", platform="instagram", user_id=settings.instagram_user_id, access_token=settings.instagram_access_token))
    cfg.spaces = [Space(id="rajasthan-si", name="Rajasthan SI", exam_ids=["rajasthan-si"], channel_ids=[c.id for c in cfg.channels],
                        schedule=SpaceSchedule(mode="daily", times=list(settings.post_times)))]
    cfg.schedules = [Schedule(name="Daily current affairs (Akhbar)", mode="daily", times=["07:00"], enabled=False)]
    return cfg


def migrate(raw: dict) -> dict:
    """Settings saved before spaces existed: exam list + content schedules -> spaces (weights dropped)."""
    if "spaces" in raw:
        return raw
    raw = dict(raw)
    exams = raw.pop("exams", None) or []
    enabled = [e["exam_id"] for e in exams if e.get("enabled", True)]
    all_channels = [c["id"] for c in raw.get("channels", []) if c.get("enabled", True)]
    spaces, keep = [], []
    for s in raw.get("schedules", []):
        if s.get("kind", "content") == "akhbar":
            keep.append(s)
            continue
        exam_ids = s.get("exam_ids") or enabled
        chans = s.get("channel_ids") or sorted({c for e in exams if e["exam_id"] in exam_ids for c in e.get("channel_ids", [])}) or all_channels
        timing = {k: s[k] for k in ("mode", "every_hours", "start_at", "times", "days") if k in s}
        spaces.append({"id": s.get("id") or _id(), "name": s.get("name") or "Space", "enabled": True, "exam_ids": exam_ids,
                       "channel_ids": chans, "language": s.get("language"), "outputs": s.get("outputs") or {},
                       "content_types": s.get("content_types", []), "video_styles": s.get("video_styles", []),
                       "schedule": {**timing, "enabled": s.get("enabled", True)}})
    if not spaces and enabled:
        spaces.append({"id": "main", "name": "Main space", "exam_ids": enabled, "channel_ids": all_channels,
                       "schedule": {"enabled": False}})
    raw["spaces"], raw["schedules"] = spaces, keep
    return raw


def storage() -> str:
    """Where panel settings live: 'supabase' (app_config table) or 'file' (control.json)."""
    from .supabase_db import configured

    return "supabase" if configured() else "file"


def _read_raw() -> tuple[dict | None, bool]:
    """(stored settings or None, whether they still need saving to the database)."""
    p = control_path()
    local = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    if storage() == "supabase":
        from .supabase_db import Supabase

        rows = Supabase().select("app_config", {"select": "data", "id": "eq.1"})
        if rows:
            return rows[0]["data"], False
        return local, local is not None  # first start on Supabase: import an existing control.json
    return local, False


def _write_raw(cfg: ControlConfig) -> None:
    if storage() == "supabase":
        from datetime import datetime, timezone

        from .supabase_db import Supabase

        Supabase().upsert("app_config", {"id": 1, "data": cfg.model_dump(mode="json"),
                                         "updated_at": datetime.now(timezone.utc).isoformat()})
        return
    p = control_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(cfg.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(p)
    try:
        os.chmod(p, 0o600)  # contains secrets
    except OSError:
        pass


def load() -> ControlConfig:
    with _lock:
        raw, needs_import = _read_raw()
        if raw is not None:
            migrated = "spaces" not in raw
            cfg = ControlConfig.model_validate(migrate(raw))
            if needs_import or migrated:
                _write_raw(cfg)
        else:
            cfg = _seed_from_env()
            save(cfg)
        # renamed paper: replace the old default titles (custom titles are kept)
        if cfg.general.akhbar_title_hi == "पाठशाला समाचार":
            cfg.general.akhbar_title_hi = General().akhbar_title_hi
        if cfg.general.akhbar_title_en == "Pathshala Times":
            cfg.general.akhbar_title_en = General().akhbar_title_en
        return cfg


def save(cfg: ControlConfig) -> None:
    with _lock:
        _write_raw(cfg)
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
        "openrouter_model": g.openrouter_model.strip() or settings.openrouter_model,
        "openrouter_fallback": g.openrouter_fallback.strip(),
        "public_base_url": (g.public_base_url.strip() or settings.public_base_url).rstrip("/"),
    }.items():
        object.__setattr__(settings, k, v)
    if s.telegram_bot_token:
        object.__setattr__(settings, "telegram_bot_token", s.telegram_bot_token)
    if s.elevenlabs_api_key:
        object.__setattr__(settings, "elevenlabs_api_key", s.elevenlabs_api_key)
    if s.gemini_api_key:
        object.__setattr__(settings, "gemini_api_key", s.gemini_api_key)
    if s.openrouter_api_key:
        object.__setattr__(settings, "openrouter_api_key", s.openrouter_api_key)
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
        exams[c.id] = Exam(id=c.id, name=c.name, short_name=c.short_name, language=c.language,
                           audience=c.audience, subjects=c.subjects, details=c.details())
    return exams


def space_exams(cfg: ControlConfig, space: Space) -> dict[str, Exam]:
    """The space's exams, with the space's language applied (exams missing from the catalogue are skipped)."""
    defs = all_exam_defs(cfg)
    return {eid: (replace(defs[eid], language=space.language) if space.language else defs[eid])
            for eid in space.exam_ids if eid in defs}


def space_channels(cfg: ControlConfig, space: Space) -> list[Channel]:
    """Channels connected to the space (enabled and configured ones only)."""
    return [c for c in cfg.channels if c.id in space.channel_ids and c.enabled and c.configured()]


def find_space(cfg: ControlConfig, key: str) -> Space | None:
    """By id, or by name (case-insensitive, spaces/hyphens ignored) - for the bot and CLI."""
    norm = lambda t: "".join(ch for ch in t.lower() if ch.isalnum())  # noqa: E731
    return cfg.space(key) or next((s for s in cfg.spaces if norm(s.name) == norm(key)), None)


def editions_for(cfg: ControlConfig, schedule: Schedule) -> list[NewsEdition]:
    eds = [e for e in cfg.editions if e.enabled]
    return [e for e in eds if e.id in schedule.edition_ids] if schedule.edition_ids else eds


def channels_for_edition(cfg: ControlConfig, edition: NewsEdition, schedule: Schedule | None = None) -> list[Channel]:
    """Telegram channels for a newspaper: schedule override > the edition's own channels > all."""
    ids = (schedule.channel_ids if schedule and schedule.channel_ids else None) or edition.channel_ids or None
    chans = [c for c in cfg.channels if c.enabled and c.configured() and c.platform == "telegram"]
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

    def check_timing(where: str, t: Timing) -> None:
        if t.mode == "interval" and not (0.25 <= t.every_hours <= 168):
            raise ValueError(f"{where}: every_hours must be between 0.25 and 168")
        for hm in ([t.start_at] if t.mode == "interval" else t.times):
            if not hhmm.match(hm):
                raise ValueError(f"{where}: time '{hm}' must be HH:MM (24h)")
        if t.mode == "daily" and not t.times:
            raise ValueError(f"{where}: add at least one time")

    for s in cfg.schedules:
        check_timing(f"Schedule '{s.name}'", s)
        bad = [x for x in s.channel_ids if x not in chan_ids] + [x for x in s.edition_ids if x not in {e.id for e in cfg.editions}]
        if bad:
            raise ValueError(f"Schedule '{s.name}': unknown values {bad}")
    space_ids = set()
    for sp in cfg.spaces:
        where = f"Space '{sp.name}'"
        if not sp.name.strip():
            raise ValueError("Every space needs a name")
        if not re.match(r"^[a-z0-9-]{2,40}$", sp.id):
            raise ValueError(f"{where}: id '{sp.id}' must be 2-40 lowercase letters, digits and hyphens")
        if sp.id in space_ids:
            raise ValueError(f"Two spaces have the id '{sp.id}'")
        space_ids.add(sp.id)
        if not sp.exam_ids:
            raise ValueError(f"{where}: add at least one exam")
        check_timing(where, sp.schedule)
        bad = [x for x in sp.content_types if x not in CONTENT_TYPES] + [x for x in sp.video_styles if x not in VIDEO_STYLES]
        bad += [x for x in sp.channel_ids if x not in chan_ids] + [x for x in sp.exam_ids if x not in exam_ids]
        if bad:
            raise ValueError(f"{where}: unknown values {bad}")
        if not 0 <= sp.memory <= 500:
            raise ValueError(f"{where}: memory must be between 0 and 500 past topics")
    series_ids = set()
    for ts in cfg.test_series:
        where = f"Test series '{ts.name}'"
        if not ts.name.strip():
            raise ValueError("Every test series needs a name")
        if not re.match(r"^[a-z0-9-]{2,40}$", ts.id):
            raise ValueError(f"{where}: id '{ts.id}' must be 2-40 lowercase letters, digits and hyphens")
        if ts.id in series_ids:
            raise ValueError(f"Two test series have the id '{ts.id}'")
        series_ids.add(ts.id)
        if ts.exam_id not in exam_ids:
            raise ValueError(f"{where}: choose an exam")
        check_timing(where, ts.schedule)
        if [x for x in ts.channel_ids if x not in chan_ids]:
            raise ValueError(f"{where}: unknown channel")
        kinds = [st.kind for st in ts.stages]
        if len(set(kinds)) != len(kinds):
            raise ValueError(f"{where}: each kind of test appears once")
        for st in ts.stages:
            if not 0 <= st.count <= 50:
                raise ValueError(f"{where}: number of {st.kind} tests must be 0-50")
            if st.questions and not 5 <= st.questions <= 200:
                raise ValueError(f"{where}: questions per subject test must be 5-200 (0 = automatic)")
    url = cfg.general.public_base_url.strip()
    if url and not re.match(r"^https?://[^\s/]+", url):
        raise ValueError("Public URL must start with https:// (e.g. https://tests.example.com)")
    if not 1 <= cfg.general.max_parallel_runs <= 8:
        raise ValueError("Parallel runs must be between 1 and 8")
    from .akhbar import regions
    from .languages import is_known

    langs = [("default language", cfg.general.default_language)]
    langs += [(f"space {s.name}", s.language) for s in cfg.spaces if s.language]
    langs += [(f"schedule {s.name}", s.language) for s in cfg.schedules if s.language]
    langs += [(f"edition {e.region}", e.language) for e in cfg.editions]
    langs += [(f"custom exam {c.name}", c.language) for c in cfg.custom_exams]
    langs += [(f"test series {t.name}", t.language) for t in cfg.test_series if t.language]
    bad_lang = [f"{where}: '{code}'" for where, code in langs if not is_known(code)]
    if bad_lang:
        raise ValueError("Unknown language " + ", ".join(bad_lang))
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
        "languages": _language_choices(),
        "regions": _region_choices(),
        "exams_yaml": str(PACKAGE_DIR / "exams.yaml"),
    }


def _language_choices() -> dict[str, str]:
    from .languages import choices

    return choices()


def _region_choices() -> list[dict]:
    from .akhbar import CENTRAL, STATE_HI

    return [{"id": CENTRAL, "label": "Central / National (राष्ट्रीय)"}] + [
        {"id": r, "label": f"{r} ({hi})"} for r, hi in sorted(STATE_HI.items())
    ]
