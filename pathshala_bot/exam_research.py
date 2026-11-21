"""Type an exam's name; the AI searches the web and builds its profile.

`lookup(name)` first matches the catalogue (built-in exams.yaml + exams added earlier), so
"Rajasthan SI" or "jee main" resolve instantly. Anything else is researched: conducting
body, pattern, language of the paper, audience, and the syllabus as subjects with seed
topics. The result is stored as a custom exam (editable in the panel) and spaces use it
like any built-in exam.
"""

from __future__ import annotations

import difflib
import logging
import re
from datetime import date

from pydantic import BaseModel, Field

from .config import Exam

log = logging.getLogger(__name__)


class SubjectTopics(BaseModel):
    subject: str = Field(description="Syllabus subject / paper section as named in the official syllabus, short.")
    topics: list[str] = Field(description="6-12 specific, frequently asked syllabus topics of this subject (seed topics).")


class ExamProfile(BaseModel):
    id: str = Field(description="Short id: lowercase English letters, digits and hyphens, 3-40 characters, e.g. 'rajasthan-patwari', 'up-police-constable'.")
    name: str = Field(description="Official full name of the exam in English, e.g. 'Rajasthan Patwari Recruitment Exam (RSMSSB)'.")
    short_name: str = Field(description="Name students use, max 24 characters, e.g. 'Rajasthan Patwari'.")
    language: str = Field(description="Language code for content: the main medium students of this exam study in. One of the codes listed in the prompt.")
    audience: str = Field(description="Who takes it (eligibility / typical aspirant), one sentence.")
    conducting_body: str = Field(description="Organisation that conducts the exam, e.g. 'Rajasthan Staff Selection Board (RSSB)'.")
    pattern: str = Field(description="Current exam pattern in max 500 characters: stages, papers, number of questions, marks, duration, negative marking.")
    subjects: list[SubjectTopics] = Field(description="4-12 subjects covering the official syllabus.")
    sources: list[str] = Field(description="URLs of the official notification / syllabus / reliable pages used.")


def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", " ", (text or "").lower(), flags=re.UNICODE).strip()


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:40].strip("-")


def match_catalogue(name: str, exams: dict[str, Exam], cutoff: float = 0.86) -> Exam | None:
    """An existing exam the typed name refers to (id, full name or short name; tolerant of case/punctuation)."""
    n = _norm(name)
    if not n:
        return None
    keys: dict[str, Exam] = {}
    for e in exams.values():
        for k in (e.id.replace("-", " "), e.name, e.short_name):
            keys.setdefault(_norm(k), e)
    if n in keys:
        return keys[n]
    close = difflib.get_close_matches(n, list(keys), n=1, cutoff=cutoff)
    return keys[close[0]] if close else None


def research_exam(name: str, default_language: str = "hi") -> ExamProfile:
    """Web research + structured profile for a typed exam name (two AI calls)."""
    from . import llm
    from .languages import LANGUAGES, is_known

    research = llm.web_research(f"""You research Indian competitive and school exams for an exam-prep channel. Today is {date.today().isoformat()}.

Exam typed by the admin: "{name}"

Use web search to identify exactly which exam this is (if the name is ambiguous, choose the most common current exam
with that name in India and say so), then find from the official notification / syllabus / conducting body's website:
1. Official full name, the name students use, and the conducting body.
2. Latest exam pattern: stages, papers, number of questions, marks, duration, negative marking.
3. Eligibility / who takes it.
4. Medium / language of the question paper and what language most aspirants study in.
5. The complete syllabus, organised by subject/section, with the specific topics listed.
6. Which topics are asked most often (previous-year paper analyses).

Reply with clear research notes in English and list the URLs you relied on.""", 6)
    codes = ", ".join(f"{c} ({l.name})" for c, l in LANGUAGES.items())
    profile = llm.structured(
        "You turn research notes about an exam into a structured exam profile. Use only what the notes support.",
        f"""Exam typed by the admin: "{name}"

Language codes: {codes}
If the medium is bilingual (e.g. Hindi and English), choose the language most aspirants of this exam study in.

RESEARCH NOTES
{research.notes}

SOURCES
""" + "\n".join(f"- {s['title']} - {s['url']}" for s in research.sources[:20]),
        ExamProfile, max_tokens=12000, effort="low")
    profile.language = profile.language if is_known(profile.language) else default_language
    profile.subjects = [s for s in profile.subjects if s.subject.strip() and s.topics]
    if not profile.subjects:
        raise RuntimeError(f"Could not find the syllabus of '{name}'. Try the full official name.")
    sources = {s["url"] for s in research.sources}
    profile.sources = list(dict.fromkeys([u for u in profile.sources if u.startswith("http")] + sorted(sources)))[:12]
    return profile


def to_custom_exam(profile: ExamProfile, taken: set[str], typed_name: str = ""):
    """CustomExam for the panel, with an id that doesn't clash with existing exams."""
    from .control import CustomExam

    base = slug(profile.id) or slug(profile.short_name) or slug(typed_name) or "exam"
    base = base if len(base) >= 2 else f"exam-{base}"
    eid, n = base, 2
    while eid in taken:
        eid = f"{base[:36]}-{n}"
        n += 1
    return CustomExam(
        id=eid, name=profile.name.strip(), short_name=profile.short_name.strip()[:30] or profile.name[:30],
        language=profile.language, audience=profile.audience.strip(),
        subjects={s.subject.strip(): [t.strip() for t in s.topics if t.strip()] for s in profile.subjects},
        conducting_body=profile.conducting_body.strip(), pattern=profile.pattern.strip()[:800],
        sources=profile.sources, researched_on=date.today().isoformat(), typed_name=typed_name or profile.short_name,
    )


def lookup(cfg, name: str, refresh_id: str | None = None) -> dict:
    """{'match': exam_id} for a known exam, else {'exam': CustomExam dict} freshly researched.

    refresh_id: re-research an existing custom exam and keep its id."""
    from . import control

    name = (name or "").strip()
    if len(name) < 2:
        raise ValueError("Type the exam's name")
    defs = control.all_exam_defs(cfg)
    if not refresh_id:
        hit = match_catalogue(name, defs)
        if hit:
            return {"match": hit.id, "name": hit.name}
    profile = research_exam(name, cfg.general.default_language)
    taken = set(defs) - ({refresh_id} if refresh_id else set())
    exam = to_custom_exam(profile, taken, name)
    if refresh_id:
        exam.id = refresh_id
    log.info("researched exam '%s' -> %s (%d subjects)", name, exam.id, len(exam.subjects))
    return {"exam": exam.model_dump()}
