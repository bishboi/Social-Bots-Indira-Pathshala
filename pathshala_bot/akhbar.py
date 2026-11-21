"""Daily current-affairs newspaper ("Akhbar") for an exam.

1. Research: Claude searches the web for the last ~24-48h of news and decides what
   matters for this exam (state news for state PSC exams, science/NTA notices for
   JEE/NEET, etc.).
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

from .config import Exam, settings
from .models import MCQ, Source
from .store import History

log = logging.getLogger(__name__)


def _last_line(text: str) -> str:
    """The informative line of a traceback (the exception message), not 'Traceback ...'."""
    lines = [l for l in text.strip().splitlines() if l.strip()]
    return (lines[-1] if lines else text)[:300]

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


class NewsArticle(BaseModel):
    headline: str = Field(description="Newspaper headline, max 12 words, factual.")
    category: Category
    date: str = Field(description="Date of the event or report, YYYY-MM-DD, exactly as found in the source.")
    dateline: str = Field(description="Place the news is from, e.g. 'जयपुर' / 'New Delhi'.")
    summary: str = Field(description="1-2 sentence standfirst summarising the story.")
    body: list[str] = Field(description="2-4 short paragraphs (total 120-200 words): what happened, background, significance.")
    key_facts: list[str] = Field(description="3-5 exam-ready facts (names, numbers, places, dates) from this story.")
    exam_angle: str = Field(description="Why this matters for the exam: which paper/topic it connects to and how it may be asked.")
    static_link: str | None = Field(default=None, description="Related static GK fact worth revising, or null.")
    importance: int = Field(description="1-5 relevance for this exam (5 = very likely to be asked).")
    sources: list[Source] = Field(description="Sources used for this story (from the research notes). Never empty.")


class AkhbarEdition(BaseModel):
    tagline: str = Field(description="Short line under the masthead, e.g. 'आज की परीक्षा-उपयोगी ख़बरें'.")
    articles: list[NewsArticle] = Field(description="8-12 articles, most important first.")
    one_liners: list[str] = Field(description="10-15 one-line current affairs facts not covered above (each with its date if relevant).")
    did_you_know: list[str] = Field(description="3-5 static GK facts linked to today's news.")
    quiz: list[MCQ] = Field(description="6-10 MCQs strictly from today's articles.")


@dataclass
class AkhbarResult:
    run_dir: Path
    exam: Exam
    edition: AkhbarEdition | None = None
    pdf: Path | None = None
    published: dict = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.pdf is not None

    def summary(self) -> str:
        lines = [f"Akhbar: {self.run_dir}", f"Exam: {self.exam.name}"]
        if self.edition:
            lines.append(f"Articles: {len(self.edition.articles)}, quiz: {len(self.edition.quiz)}")
        if self.pdf:
            lines.append(f"PDF: {self.pdf}")
        lines += [f"Published {k}" for k in self.published]
        lines += [f"ERROR {k}: {_last_line(v)}" for k, v in self.errors.items()]
        return "\n".join(lines)


def today() -> date:
    return datetime.now(ZoneInfo(settings.post_timezone)).date()


def research_news(exam: Exam, day: date, avoid: list[str]):
    from . import llm

    avoid_txt = "\n".join(f"- {h}" for h in avoid[:60]) or "- (none)"
    prompt = f"""You are the news desk of "{settings.brand_name}", which publishes a daily current-affairs paper for exam aspirants.
Today is {day.isoformat()} ({day.strftime('%A')}).

Exam: {exam.name}
Audience: {exam.audience}

Use web search to find the news from the last 24-48 hours (at most {MAX_AGE_DAYS} days old) that matters most for THIS exam.
You decide what is important, the way a top coaching institute would: e.g. for a state PSC / police exam put state news
(government schemes, appointments, budgets, culture, geography, state-level awards) first, then national and international;
for JEE/NEET focus on science and technology, health, environment and official exam notices (NTA, dates, syllabus); for
school boards, education-policy and general-awareness items. Skip pure politics, crime and celebrity news unless clearly exam-relevant.

Prefer official and reputable sources (PIB, government portals, The Hindu, Indian Express, state DIPR, Dainik Bhaskar, Rajasthan Patrika,
NTA, ISRO, RBI). Verify names, numbers and dates against at least one reliable source.

Already published in recent editions (do not repeat unless there is a genuinely new development):
{avoid_txt}

Reply with research notes in English: 12-16 candidate stories, each as
STORY: <headline>
DATE: <YYYY-MM-DD of the event/report>
PLACE: <dateline>
FACTS: <bullet facts with names, numbers, dates>
WHY IT MATTERS FOR THE EXAM: <1-2 lines>
SOURCES: <title - url> (one or more)
Then 15 extra one-line facts (with dates) and their sources."""
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": max(settings.max_web_searches, 12)}]
    return llm.web_research(prompt, tools)


def write_edition(exam: Exam, day: date, research) -> AkhbarEdition:
    from . import llm

    src_list = "\n".join(f"- {s['title']} - {s['url']}" for s in research.sources[:60])
    system = f"""You are the editor of a daily current-affairs newspaper for {exam.name} aspirants, published by "{settings.brand_name}".
- {llm.LANG_RULES.get(exam.language, llm.LANG_RULES['en'])}
- Use only facts from the research notes. Never invent numbers, names, quotes or dates. If a detail is unclear, leave it out.
- Write like a quality newspaper: neutral, clear, short sentences, no opinions.
- Every article must have a 'date' from the notes and at least one source URL from the notes.
- Order articles by importance for this exam. Quiz questions must be answerable from today's articles, with exactly 4 options."""
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


def render(ed: AkhbarEdition, exam: Exam, day: date, issue_no: int, out: Path, title: str) -> Path:
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    from weasyprint import HTML

    from .config import PACKAGE_DIR
    from .render import fonts

    env = Environment(loader=FileSystemLoader(PACKAGE_DIR / "templates"), autoescape=select_autoescape(["html"]))
    hi = exam.language == "hi"
    idx = fonts._index()

    def face(name, fallback):
        p = idx.get(name) or fonts.font_path(*fallback)
        return Path(p).resolve().as_uri()

    months_hi = ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"]
    days_hi = ["सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"]
    date_str = f"{days_hi[day.weekday()]}, {day.day} {months_hi[day.month - 1]} {day.year}" if hi else day.strftime("%A, %d %B %Y")
    html = env.get_template("akhbar.html").render(
        ed=ed, exam=exam, title=title, date_str=date_str, issue_no=issue_no, hi=hi,
        cat=lambda c: CATEGORY_LABELS.get(c, CATEGORY_LABELS["other"])[0 if hi else 1],
        serif_regular=face("NotoSerifDevanagari-Regular.ttf", ("deva", "regular")),
        serif_bold=face("NotoSerifDevanagari-Bold.ttf", ("deva", "bold")),
        sans_regular=fonts.font_path("deva" if hi else "latin", "regular").resolve().as_uri(),
        sans_bold=fonts.font_path("deva" if hi else "latin", "bold").resolve().as_uri(),
        logo_url=settings.brand_logo.resolve().as_uri() if settings.brand_logo.exists() else None,
        brand=settings.brand_name, handle=settings.brand_handle, owner=settings.copyright_owner or settings.brand_name,
        year=day.year, watermark=settings.pdf_watermark,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".html").write_text(html, encoding="utf-8")
    HTML(string=html, base_url=str(out.parent)).write_pdf(str(out))
    return out


def publish_telegram(ed: AkhbarEdition, exam: Exam, pdf: Path, title: str, date_str: str, chat_id: str, questions: bool = True) -> dict:
    import time

    from .publish.telegram import Telegram, safe_html

    tg = Telegram()
    heads = "\n".join(f"▪️ {safe_html(a.headline)}" for a in ed.articles[:6])
    label = "आज के मुख्य समाचार" if exam.language == "hi" else "Today's top stories"
    cap = f"🗞 <b>{safe_html(title)}</b>\n{safe_html(date_str)}\n\n<b>{label}</b>\n{heads}\n\n📥 PDF ⬆️ | {safe_html(settings.brand_handle)}"
    res = {"chat_id": chat_id, "messages": [tg.send_document(chat_id, pdf, cap[:1024])["message_id"]]}
    if questions:
        for q in ed.quiz[:10]:
            res["messages"].append(tg.send_quiz(chat_id, q.question, q.options, q.correct_index, q.explanation)["message_id"])
            time.sleep(1.2)
    return res


def run(exam: Exam, channels: list | None = None, questions: bool = True, day: date | None = None, from_json: Path | None = None,
        title: str | None = None) -> AkhbarResult:
    """Build (and, if channels are given, publish) one edition for one exam."""
    from . import llm

    day = day or today()
    history = History(settings.db_path)
    run_dir = settings.output_dir / f"{day.strftime('%Y%m%d')}-{datetime.now().strftime('%H%M%S')}_{exam.id}_akhbar"
    run_dir.mkdir(parents=True, exist_ok=True)
    result = AkhbarResult(run_dir, exam)
    hi = exam.language == "hi"
    title = title or (settings.akhbar_title_hi if hi else settings.akhbar_title_en) or (
        "पाठशाला समाचार" if hi else "Pathshala Times")

    try:
        if from_json:
            ed = AkhbarEdition.model_validate_json(Path(from_json).read_text(encoding="utf-8"))
        else:
            research = research_news(exam, day, history.recent_headlines(exam.id))
            (run_dir / "research.md").write_text(research.notes + "\n\nSOURCES\n" + "\n".join(f"- {s['title']}: {s['url']}" for s in research.sources), encoding="utf-8")
            ed = write_edition(exam, day, research)
        dropped = check_edition(ed, day) if not from_json else []
        if dropped:
            log.info("akhbar checks: %s", dropped)
        if not ed.articles:
            raise RuntimeError("no usable articles after checks: " + "; ".join(dropped))
        result.edition = ed
        (run_dir / "akhbar.json").write_text(ed.model_dump_json(indent=2), encoding="utf-8")
        issue_no = history.akhbar_count(exam.id) + 1
        result.pdf = render(ed, exam, day, issue_no, run_dir / f"akhbar_{exam.id}_{day.isoformat()}.pdf", title)
    except Exception:
        log.exception("akhbar build failed")
        result.errors["build"] = traceback.format_exc()

    if result.pdf and channels:
        date_str = day.strftime("%d-%m-%Y")
        for ch in channels:
            if ch.platform != "telegram":
                continue
            key = f"telegram:{ch.name}"
            try:
                result.published[key] = publish_telegram(result.edition, exam, result.pdf, title, date_str, ch.chat_id, questions)
            except Exception:
                log.exception("akhbar publish failed")
                result.errors[key] = traceback.format_exc()

    (run_dir / "manifest.json").write_text(json.dumps({
        "kind": "akhbar", "exam": exam.id, "date": day.isoformat(), "pdf": str(result.pdf) if result.pdf else None,
        "articles": [a.headline for a in result.edition.articles] if result.edition else [],
        "published": result.published, "errors": result.errors,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    status = "ok" if result.ok and not result.errors else ("partial" if result.ok else "failed")
    lead = result.edition.articles[0].headline if result.edition and result.edition.articles else ""
    history.record(exam.id, "Current Affairs", lead, "akhbar", str(run_dir), status, result.published)
    if result.edition:
        history.add_headlines(exam.id, day.isoformat(), [a.headline for a in result.edition.articles])
    return result
