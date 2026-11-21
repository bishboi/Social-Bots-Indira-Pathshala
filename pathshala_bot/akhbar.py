"""Daily current-affairs newspaper ("Akhbar"), one edition per region + language.

An edition is either a state/UT ("Rajasthan" -> same paper for every Rajasthan exam:
RPSC, police, RSSB, REET...) or "central" (national paper for UPSC, SSC, banking,
railways, defence...), in Hindi, English or Hinglish.

1. Research: Claude searches the web for the last ~24-48h of news and decides what
   matters for that region's competitive exams.
2. Edition: structured articles (headline, body, key facts, exam angle, sources),
   one-liners, "did you know" static links and a quiz.
3. Checks: stale or undated stories and stories without sources are dropped.
4. Render a newspaper-style PDF and post it to Telegram.
"""

from __future__ import annotations

import json
import logging
import re
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from .config import settings
from .models import MCQ, Source
from .store import get_history

log = logging.getLogger(__name__)


def _last_line(text: str) -> str:
    """The informative line of a traceback (the exception message), not 'Traceback ...'."""
    from .providers import friendly_error

    lines = [l for l in text.strip().splitlines() if l.strip()]
    return friendly_error((lines[-1] if lines else text))[:300]

MAX_AGE_DAYS = 3

CATEGORY_LABELS = {
    "national": ("राष्ट्रीय", "National"),
    "state": ("राज्य", "State"),
    "international": ("अंतरराष्ट्रीय", "International"),
    "economy": ("अर्थव्यवस्था", "Economy"),
    "polity": ("राजव्यवस्था", "Polity & Governance"),
    "schemes": ("योजनाएँ", "Schemes"),
    "science_tech": ("विज्ञान-प्रौद्योगिकी", "Science & Tech"),
    "environment": ("पर्यावरण", "Environment"),
    "defence": ("रक्षा", "Defence"),
    "sports": ("खेल", "Sports"),
    "awards_appointments": ("पुरस्कार-नियुक्तियाँ", "Awards & Appointments"),
    "exam_notice": ("परीक्षा सूचना", "Exam Notice"),
    "other": ("विविध", "Miscellaneous"),
}
Category = Literal[tuple(CATEGORY_LABELS)]  # type: ignore[valid-type]


CENTRAL = "central"

# Hindi names for state editions (keys match the official boundary data in geo.py)
STATE_HI = {
    "Andaman & Nicobar": "अंडमान और निकोबार", "Andhra Pradesh": "आंध्र प्रदेश", "Arunachal Pradesh": "अरुणाचल प्रदेश",
    "Assam": "असम", "Bihar": "बिहार", "Chandigarh": "चंडीगढ़", "Chhattisgarh": "छत्तीसगढ़",
    "Dadra and Nagar Haveli and Daman and Diu": "दादरा और नगर हवेली और दमन और दीव", "Delhi": "दिल्ली", "Goa": "गोवा",
    "Gujarat": "गुजरात", "Haryana": "हरियाणा", "Himachal Pradesh": "हिमाचल प्रदेश", "Jammu & Kashmir": "जम्मू और कश्मीर",
    "Jharkhand": "झारखंड", "Karnataka": "कर्नाटक", "Kerala": "केरल", "Ladakh": "लद्दाख", "Lakshadweep": "लक्षद्वीप",
    "Madhya Pradesh": "मध्य प्रदेश", "Maharashtra": "महाराष्ट्र", "Manipur": "मणिपुर", "Meghalaya": "मेघालय",
    "Mizoram": "मिज़ोरम", "Nagaland": "नागालैंड", "Odisha": "ओडिशा", "Puducherry": "पुडुचेरी", "Punjab": "पंजाब",
    "Rajasthan": "राजस्थान", "Sikkim": "सिक्किम", "Tamil Nadu": "तमिलनाडु", "Telangana": "तेलंगाना", "Tripura": "त्रिपुरा",
    "Uttar Pradesh": "उत्तर प्रदेश", "Uttarakhand": "उत्तराखंड", "West Bengal": "पश्चिम बंगाल",
}


def regions() -> list[str]:
    """'central' plus every state/UT, in the spelling used by the boundary data."""
    return [CENTRAL] + sorted(STATE_HI)


@dataclass(frozen=True)
class Desk:
    """One newspaper edition: a region (state or central) in one language."""

    region: str
    language: str = "hi"
    id: str = ""

    @property
    def key(self) -> str:
        """Stable id used for history / dedup, e.g. 'rajasthan-hi', 'central-en'."""
        return self.id or f"{re.sub(r'[^a-z0-9]+', '-', self.region.lower()).strip('-')}-{self.language}"

    @property
    def central(self) -> bool:
        return self.region == CENTRAL

    def label(self) -> str:
        from . import i18n

        if self.central:
            return i18n.t(self.language, "national_edition")
        return i18n.t(self.language, "edition", region=i18n.region_name(self.language, self.region))


def desk_brief(desk: Desk) -> str:
    """What this edition covers and for whom - shared by research and writing."""
    if desk.central:
        return """NATIONAL (CENTRAL) EDITION - for aspirants of central government exams: UPSC (civil services, CDS, NDA),
SSC (CGL, CHSL, GD), banking and insurance (IBPS, SBI, RBI, LIC), railways (RRB), defence, CTET and NTA exams.
Mix: ~65% national (Union government decisions, schemes, economy and RBI, polity and governance, science and technology,
ISRO/DRDO, environment, defence, reports and indices, awards, appointments, sports, important days) and ~35% international
(India's bilateral relations, summits, organisations). Prefer PIB, ministry websites, RBI, ISRO and major national papers."""
    return f"""{desk.region.upper()} STATE EDITION - one paper for aspirants of ALL {desk.region} government exams: the state public
service commission (state civil services), police (SI, constable), staff/subordinate selection board exams (patwari, clerk,
gram sevak...), teacher recruitment and the state board. It is the same paper whichever of these exams the reader takes.
Mix: ~55% {desk.region} news (state government decisions, cabinet, schemes, budget, appointments, state awards, culture
and heritage, festivals, geography and environment, economy, infrastructure, sports, district-level firsts, recruitment
notices), ~30% national and ~15% international. Prefer the official {desk.region} government and public-relations
(DIPR / jansampark) portals, the state PSC/board websites and the leading newspapers of {desk.region}, plus PIB for national news."""


class NewsArticle(BaseModel):
    headline: str = Field(description="Newspaper headline, max 12 words, factual.")
    category: Category
    date: str = Field(description="Date of the event or report, YYYY-MM-DD, exactly as found in the source.")
    dateline: str = Field(description="Place the news is from, e.g. 'जयपुर' / 'New Delhi'.")
    summary: str = Field(description="1-2 sentence standfirst summarising the story.")
    body: list[str] = Field(description="2-4 short paragraphs (total 120-200 words): what happened, background, significance.")
    key_facts: list[str] = Field(description="3-5 exam-ready facts (names, numbers, places, dates) from this story.")
    exam_angle: str = Field(description="Why aspirants should care: which exams/papers and syllabus topic it connects to, and how it may be asked.")
    static_link: str | None = Field(default=None, description="Related static GK fact worth revising, or null.")
    importance: int = Field(description="1-5 relevance for this edition's exams (5 = very likely to be asked).")
    sources: list[Source] = Field(description="Sources used for this story (from the research notes). Never empty.")
    image_topic: str | None = Field(
        default=None,
        description="Exact English Wikipedia article title whose lead photo genuinely illustrates this story: the place, monument, "
        "institution, river, spacecraft or the office-holder at the centre of it (e.g. 'Hawa Mahal', 'Reserve Bank of India'). Null if none fits.",
    )


# Newspaper sections (front page first, then these in order); category -> section
SECTIONS = [
    ("state", ("राज्य", "State"), {"state"}),
    ("nation", ("देश", "Nation"), {"national", "polity", "schemes", "defence"}),
    ("exam", ("परीक्षा व भर्ती", "Exams & Jobs"), {"exam_notice"}),
    ("economy", ("अर्थव्यवस्था", "Economy"), {"economy"}),
    ("world", ("विदेश", "World"), {"international"}),
    ("science", ("विज्ञान व पर्यावरण", "Science & Environment"), {"science_tech", "environment"}),
    ("people", ("पुरस्कार व नियुक्तियाँ", "Awards & Appointments"), {"awards_appointments"}),
    ("sports", ("खेल", "Sports"), {"sports"}),
    ("misc", ("विविध", "Miscellany"), {"other"}),
]
FRONT_PAGE_STORIES = 4  # lead + 3


def layout(articles: list) -> tuple[list[tuple[int, object]], list[tuple[str, list[tuple[int, object]]]]]:
    """Split ranked articles into the front page and the inside sections (keeping their index for anchors)."""
    numbered = list(enumerate(articles))
    front, rest = numbered[:FRONT_PAGE_STORIES], numbered[FRONT_PAGE_STORIES:]
    sections = []
    for key, _, cats in SECTIONS:
        items = [(i, a) for i, a in rest if a.category in cats]
        if items:
            sections.append((key, items))
    return front, sections


def section_label_for(key: str, language: str) -> str:
    from . import i18n

    return i18n.t(language, f"sec_{key}")


def section_label(key: str, hi: bool) -> str:
    return next(lbl for k, lbl, _ in SECTIONS if k == key)[0 if hi else 1]


class AkhbarEdition(BaseModel):
    tagline: str = Field(description="Short line under the masthead, e.g. 'आज की परीक्षा-उपयोगी ख़बरें'.")
    articles: list[NewsArticle] = Field(description="8-12 articles, most important first.")
    one_liners: list[str] = Field(description="10-15 one-line current affairs facts not covered above (each with its date if relevant).")
    did_you_know: list[str] = Field(description="3-5 static GK facts linked to today's news.")
    quiz: list[MCQ] = Field(description="6-10 MCQs strictly from today's articles.")


@dataclass
class AkhbarResult:
    run_dir: Path
    desk: Desk
    edition: AkhbarEdition | None = None
    pdf: Path | None = None
    published: dict = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.pdf is not None

    def summary(self) -> str:
        lines = [f"Akhbar: {self.run_dir}", f"Edition: {self.desk.label()} ({self.desk.key})"]
        if self.edition:
            lines.append(f"Articles: {len(self.edition.articles)}, quiz: {len(self.edition.quiz)}")
        if self.pdf:
            lines.append(f"PDF: {self.pdf}")
        lines += [f"Published {k}" for k in self.published]
        lines += [f"ERROR {k}: {_last_line(v)}" for k, v in self.errors.items()]
        return "\n".join(lines)


def today() -> date:
    return datetime.now(ZoneInfo(settings.post_timezone)).date()


def research_news(desk: Desk, day: date, avoid: list[str]):
    from . import llm

    avoid_txt = "\n".join(f"- {h}" for h in avoid[:60]) or "- (none)"
    prompt = f"""You are the news desk of "{settings.brand_name}", which publishes a daily current-affairs paper for exam aspirants.
Today is {day.isoformat()} ({day.strftime('%A')}).

{desk_brief(desk)}

Use web search to find the news from the last 24-48 hours (at most {MAX_AGE_DAYS} days old) that matters most for these readers.
You decide what is important, the way a top coaching institute would. Skip party politics, crime and celebrity news unless
clearly exam-relevant. Verify names, numbers and dates against at least one reliable source.

Already published in recent editions of this paper (do not repeat unless there is a genuinely new development):
{avoid_txt}

Reply with research notes in English: 12-16 candidate stories, each as
STORY: <headline>
SCOPE: <state | national | international>
DATE: <YYYY-MM-DD of the event/report>
PLACE: <dateline>
FACTS: <bullet facts with names, numbers, dates>
WHY IT MATTERS FOR THE EXAMS: <1-2 lines>
SOURCES: <title - url> (one or more)
Then 15 extra one-line facts (with dates) and their sources."""
    return llm.web_research(prompt, max(settings.max_web_searches, 12))


def write_edition(desk: Desk, day: date, research) -> AkhbarEdition:
    from . import llm

    src_list = "\n".join(f"- {s['title']} - {s['url']}" for s in research.sources[:60])
    system = f"""You are the editor of a daily current-affairs newspaper published by "{settings.brand_name}".
{desk_brief(desk)}
- {llm.lang_rule(desk.language)}
- Use only facts from the research notes. Never invent numbers, names, quotes or dates. If a detail is unclear, leave it out.
- Write like a quality newspaper: neutral, clear, short sentences, no opinions.
- Every article must have a 'date' from the notes and at least one source URL from the notes.
- Give the lead story (and other stories where a real photo helps) an 'image_topic' - only the place, monument,
  institution or office-holder the story is about. Photos are fetched from Wikimedia Commons, so never describe a photo.
- Use category 'state' for {desk.region} news{' (there is none in the national edition)' if desk.central else ''}.
- Order articles by importance for these readers. Quiz questions must be answerable from today's articles, with exactly 4 options."""
    user = f"""Edition date: {day.isoformat()}

RESEARCH NOTES
{research.notes}

SOURCES FOUND
{src_list or '- none'}

Write today's edition."""
    return llm.structured(system, user, AkhbarEdition, max_tokens=48000)


def check_edition(ed: AkhbarEdition, day: date) -> list[str]:
    """Drop stale / undated / unsourced stories; returns notes about what was removed."""
    notes, keep = [], []
    for a in ed.articles:
        try:
            d = date.fromisoformat(a.date[:10])
        except ValueError:
            notes.append(f"dropped (no valid date): {a.headline}")
            continue
        if d > day + timedelta(days=1) or (day - d).days > MAX_AGE_DAYS:
            notes.append(f"dropped (dated {a.date}): {a.headline}")
            continue
        a.sources = [s for s in a.sources if s.url.startswith("http")]
        if not a.sources:
            notes.append(f"dropped (no source): {a.headline}")
            continue
        a.importance = max(1, min(5, a.importance))
        keep.append(a)
    keep.sort(key=lambda a: -a.importance)
    ed.articles = keep[: max(4, settings.akhbar_articles)]
    clean = []
    for q in ed.quiz:
        opts = [o.strip()[:100] for o in q.options if o.strip()]
        if len(opts) >= 2 and 0 <= q.correct_index < len(opts):
            q.options, q.question, q.explanation = opts, q.question[:300], q.explanation[:200]
            clean.append(q)
    ed.quiz = clean
    return notes


def fetch_photos(ed: AkhbarEdition, dest: Path, limit: int = 4) -> dict[int, dict]:
    """Freely licensed lead photos for up to `limit` stories (most important first)."""
    from . import resources

    photos: dict[int, dict] = {}
    for i, a in enumerate(ed.articles):
        if len(photos) >= limit:
            break
        if not a.image_topic:
            continue
        try:
            media = resources.fetch_image(a.image_topic, dest, width=1200)
        except Exception:
            log.exception("photo fetch failed for %s", a.image_topic)
            media = None
        if media:
            photos[i] = {"url": media.path.resolve().as_uri(), "credit": media.credit.split(": ", 1)[-1]}
    return photos


def render(ed: AkhbarEdition, desk: Desk, day: date, issue_no: int, out: Path, title: str, photos: dict | None = None) -> Path:
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    from weasyprint import HTML

    from .config import PACKAGE_DIR
    from .render import fonts

    from . import i18n
    from .languages import get as get_language

    env = Environment(loader=FileSystemLoader(PACKAGE_DIR / "templates"), autoescape=select_autoescape(["html"]))
    lang = get_language(desk.language)
    code = lang.code
    hi = code == "hi"
    labels = i18n.labels(code)

    def uri(path) -> str:
        return path.resolve().as_uri()

    # Font roles per script: Hindi = Martel + Rozha One; English/Hinglish = Source Serif 4 + Playfair +
    # blackletter masthead; other scripts = Noto Serif/Sans <Script> (Urdu: Nastaliq). Mukta covers labels.
    faces = {
        "body": uri(fonts.for_language(code, "serif")), "body_bold": uri(fonts.for_language(code, "serif_bold")),
        "head": uri(fonts.for_language(code, "head")), "mast": uri(fonts.for_language(code, "display")),
        "sans": uri(fonts.for_language(code, "regular")), "sans_bold": uri(fonts.for_language(code, "bold")),
        "head_alt": uri(fonts.font_path("deva", "head")), "latin_sans": uri(fonts.font_path("latin", "regular")),
    }
    title_top, title_main = _title_parts(title, labels)
    mast_pt = _fit_pt(title_main, fonts.for_language(code, "display"), max_pt=37 if not lang.script == "latin" else 34, width_mm=112)

    date_str = i18n.format_date(day, code)
    front, sections = layout(ed.articles)
    html = env.get_template("akhbar.html").render(
        ed=ed, desk=desk, edition_label=desk.label(), title=title, date_str=date_str, issue_no=issue_no, hi=hi,
        lang=code, rtl=lang.rtl, T=labels, latin=lang.script == "latin",
        front=front, sections=sections, section_label=lambda k: labels.get(f"sec_{k}", k), photos=photos or {},
        faces=faces, title_top=title_top, title_main=title_main, mast_pt=mast_pt,
        justify=lang.script in ("latin", "deva", "arab", "beng", "guru", "gujr", "orya"),
        motto=labels["motto"],
        cat=lambda c: labels.get(f"cat_{c}", labels["cat_other"]),
        logo_url=settings.brand_logo.resolve().as_uri() if settings.brand_logo.exists() else None,
        brand=settings.brand_name, handle=settings.brand_handle, owner=settings.copyright_owner or settings.brand_name,
        year=day.year, watermark=settings.pdf_watermark,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".html").write_text(html, encoding="utf-8")
    HTML(string=html, base_url=str(out.parent)).write_pdf(str(out))
    return out


def _title_parts(title: str, labels: dict) -> tuple[str, str]:
    """Masthead = small brand line + big title. Uses the translated parts when the title is the default one."""
    if title == labels.get("akhbar_title") and labels.get("akhbar_title_top") and labels.get("akhbar_title_main"):
        return labels["akhbar_title_top"], labels["akhbar_title_main"]
    for brand in (labels.get("akhbar_title_top"), "Indira Pathshala", "इंदिरा पाठशाला"):
        if brand and title.startswith(brand + " "):
            return brand, title[len(brand) + 1:]
    words = title.split()
    return (" ".join(words[:2]), " ".join(words[2:])) if len(words) >= 3 else ("", title)


def _fit_pt(text: str, font_file, max_pt: float, width_mm: float, min_pt: float = 18) -> float:
    """Largest point size (<= max_pt) at which `text` fits width_mm in this font."""
    try:
        from PIL import ImageFont

        width = ImageFont.truetype(str(font_file), 100).getlength(text)  # in points at 100 pt
        if width <= 0:
            return max_pt
        fit = 100 * (width_mm / 0.3528) / width
        return round(max(min_pt, min(max_pt, fit)), 1)
    except Exception:
        return max_pt


def publish_telegram(ed: AkhbarEdition, desk: Desk, pdf: Path, title: str, date_str: str, chat_id: str, questions: bool = True) -> dict:
    import time

    from .publish.telegram import Telegram, safe_html

    tg = Telegram()
    heads = "\n".join(f"▪️ {safe_html(a.headline)}" for a in ed.articles[:6])
    from . import i18n

    label = i18n.t(desk.language, "top_stories")
    cap = f"🗞 <b>{safe_html(title)}</b> · {safe_html(desk.label())}\n{safe_html(date_str)}\n\n<b>{label}</b>\n{heads}\n\n📥 PDF ⬆️ | {safe_html(settings.brand_handle)}"
    res = {"chat_id": chat_id, "messages": [tg.send_document(chat_id, pdf, cap[:1024])["message_id"]]}
    if questions:
        for q in ed.quiz[:10]:
            res["messages"].append(tg.send_quiz(chat_id, q.question, q.options, q.correct_index, q.explanation)["message_id"])
            time.sleep(1.2)
    return res


def run(desk: Desk, channels: list | None = None, questions: bool = True, day: date | None = None, from_json: Path | None = None,
        title: str | None = None) -> AkhbarResult:
    """Build (and, if channels are given, publish) one edition."""
    from . import llm  # noqa: F401  (imported for side effects in tests)

    day = day or today()
    history = get_history()
    run_dir = settings.output_dir / f"{day.strftime('%Y%m%d')}-{datetime.now().strftime('%H%M%S')}_akhbar_{desk.key}"
    run_dir.mkdir(parents=True, exist_ok=True)
    result = AkhbarResult(run_dir, desk)
    hi = desk.language == "hi"
    from . import i18n

    if not title:
        if hi:
            title = settings.akhbar_title_hi
        elif desk.language in ("en", "hinglish"):
            title = settings.akhbar_title_en
        title = title or i18n.t(desk.language, "akhbar_title")
    hkey = f"akhbar:{desk.key}"

    try:
        if from_json:
            ed = AkhbarEdition.model_validate_json(Path(from_json).read_text(encoding="utf-8"))
        else:
            research = research_news(desk, day, history.recent_headlines(hkey))
            (run_dir / "research.md").write_text(research.notes + "\n\nSOURCES\n" + "\n".join(f"- {s['title']}: {s['url']}" for s in research.sources), encoding="utf-8")
            ed = write_edition(desk, day, research)
        dropped = check_edition(ed, day) if not from_json else []
        if dropped:
            log.info("akhbar checks: %s", dropped)
        if not ed.articles:
            raise RuntimeError("no usable articles after checks: " + "; ".join(dropped))
        result.edition = ed
        (run_dir / "akhbar.json").write_text(ed.model_dump_json(indent=2), encoding="utf-8")
        issue_no = history.akhbar_count(hkey) + 1
        photos = fetch_photos(ed, run_dir / "media")
        result.pdf = render(ed, desk, day, issue_no, run_dir / f"akhbar_{desk.key}_{day.isoformat()}.pdf", title, photos)
    except Exception:
        log.exception("akhbar build failed")
        result.errors["build"] = traceback.format_exc()

    if result.pdf and channels:
        date_str = i18n.format_date(day, desk.language)
        for ch in channels:
            if ch.platform != "telegram":
                continue
            key = f"telegram:{ch.name}"
            try:
                result.published[key] = publish_telegram(result.edition, desk, result.pdf, title, date_str, ch.chat_id, questions)
            except Exception:
                log.exception("akhbar publish failed")
                result.errors[key] = traceback.format_exc()

    (run_dir / "manifest.json").write_text(json.dumps({
        "kind": "akhbar", "edition": desk.key, "region": desk.region, "language": desk.language, "date": day.isoformat(), "pdf": str(result.pdf) if result.pdf else None,
        "articles": [a.headline for a in result.edition.articles] if result.edition else [],
        "published": result.published, "errors": result.errors,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    status = "ok" if result.ok and not result.errors else ("partial" if result.ok else "failed")
    lead = result.edition.articles[0].headline if result.edition and result.edition.articles else ""
    history.record(hkey, desk.label(), lead, "akhbar", str(run_dir), status, result.published,
                   content=result.edition.model_dump(mode="json") if result.edition else None)
    if result.edition:
        history.add_headlines(hkey, day.isoformat(), [a.headline for a in result.edition.articles])
    return result
