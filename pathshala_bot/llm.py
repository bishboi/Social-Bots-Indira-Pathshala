"""Claude calls: web research, then structured content generation."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

import anthropic

from .config import Exam, settings
from .models import CONTENT_TYPES, ContentPackage

log = logging.getLogger(__name__)

# Server-side fallback: if a safety classifier declines (false positives happen,
# e.g. on law / medicine topics), the API re-runs the request on a recommended
# fallback model inside the same call instead of failing the run.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

LANG_RULES = {
    "hi": "Write ALL learner-facing text in Hindi (Devanagari script). Keep standard technical terms, formulas, section numbers and proper nouns as commonly written in Hindi-medium exam material.",
    "en": "Write all learner-facing text in clear Indian English.",
    "hinglish": "Write in simple Hinglish (Hindi in Roman script mixed with English terms), the way popular Indian educators talk. Keep formulas and technical terms in English.",
}


class ContentRefused(RuntimeError):
    pass


@dataclass
class Research:
    notes: str
    sources: list[dict] = field(default_factory=list)


_client: anthropic.Anthropic | None = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(max_retries=4)
    return _client


def _check_refusal(resp) -> None:
    if resp.stop_reason == "refusal":
        details = getattr(resp, "stop_details", None)
        raise ContentRefused(f"Claude declined this request: {getattr(details, 'category', None)} {getattr(details, 'explanation', '')}")


def research_topic(exam: Exam, subject: str, seed_topic: str, content_type: str, avoid: list[str]) -> Research:
    """Search the web for a specific, high-yield sub-topic and gather verified facts."""
    avoid_txt = "\n".join(f"- {t}" for t in avoid[:30]) or "- (none yet)"
    prompt = f"""You are the research desk of "{settings.brand_name}", an Indian exam-prep channel.
Today is {date.today().isoformat()}.

Target exam: {exam.name}
Audience: {exam.audience}
Subject: {subject}
Seed topic: {seed_topic}
Planned format: {content_type} - {CONTENT_TYPES[content_type]}

Recently covered topics (pick something different):
{avoid_txt}

Use web search to:
1. Confirm the current syllabus / exam pattern for this subject and find which sub-topics of the seed topic are asked most often (previous-year papers, answer keys, topper notes, official syllabus PDFs).
2. Choose ONE specific, high-yield sub-topic of the seed topic that fits the planned format.
3. Collect accurate facts, numbers, dates, formulas, legal section numbers etc. for it. Cross-check anything that sources disagree on and prefer official / authoritative sources. For current_affairs, only use developments from the last 6 months.
4. If you find real previous-year questions on it, record them verbatim with exam name and year.

Reply with research notes in English (the writer will translate):
CHOSEN TOPIC: <one line>
WHY HIGH-YIELD: <1-2 lines>
FACTS: bullet list of verified facts
PREVIOUS-YEAR QUESTIONS: list (or "none found")
COMMON MISTAKES: bullet list
SOURCES: list of "title - url" you actually relied on"""

    messages: list[dict] = [{"role": "user", "content": prompt}]
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": settings.max_web_searches}]
    sources: dict[str, str] = {}
    text_parts: list[str] = []

    for _ in range(6):  # pause_turn continuations
        with client().beta.messages.stream(
            model=settings.claude_model,
            max_tokens=32000,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            output_config={"effort": settings.research_effort},
            tools=tools,
            messages=messages,
        ) as stream:
            resp = stream.get_final_message()
        _check_refusal(resp)

        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
                for c in getattr(block, "citations", None) or []:
                    url = getattr(c, "url", None)
                    if url:
                        sources.setdefault(url, getattr(c, "title", "") or url)
            elif block.type == "web_search_tool_result":
                results = block.content
                if isinstance(results, list):  # an error result is a single object
                    for r in results:
                        if getattr(r, "url", None):
                            sources.setdefault(r.url, getattr(r, "title", "") or r.url)

        if resp.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": resp.content})
            continue
        break

    notes = "".join(text_parts).strip()
    if not notes:
        raise RuntimeError("Research step returned no text")
    log.info("research done: %d chars, %d sources", len(notes), len(sources))
    return Research(notes=notes, sources=[{"title": t, "url": u} for u, t in sources.items()])


def generate_package(exam: Exam, subject: str, content_type: str, research: Research) -> ContentPackage:
    """Turn research notes into every asset (quiz, short script, notes...) in one structured call."""
    src_list = "\n".join(f"- {s['title']} - {s['url']}" for s in research.sources[:25])
    system = f"""You are the head teacher and content writer of "{settings.brand_name}" ({settings.brand_handle}), an Indian exam-prep brand that posts on Telegram, Instagram Reels and YouTube Shorts.

Standards:
- Accuracy first: use only facts supported by the research notes. If something is uncertain, leave it out.
- Exam-oriented: match the level, style and vocabulary of {exam.name}.
- {LANG_RULES.get(exam.language, LANG_RULES['en'])}
- MCQs must have exactly 4 options and exactly one correct answer; distractors should be plausible. Vary the position of the correct option.
- Short-video slides are read on a phone in 3-5 seconds each: very few words on screen, the narration carries the explanation.
- Never claim a question is a previous-year question unless the research notes show it is."""

    user = f"""Exam: {exam.name} ({exam.short_name})
Audience: {exam.audience}
Subject: {subject}
Format: {content_type} - {CONTENT_TYPES[content_type]}

RESEARCH NOTES
{research.notes}

SOURCES FOUND
{src_list or '- none'}

Create the complete content package for this topic."""

    resp = client().beta.messages.parse(
        model=settings.claude_model,
        max_tokens=32000,
        betas=[FALLBACK_BETA],
        fallbacks="default",
        output_config={"effort": settings.generation_effort},
        system=system,
        messages=[{"role": "user", "content": user}],
        output_format=ContentPackage,
    )
    _check_refusal(resp)
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("Generation hit max_tokens; output truncated")
    pkg = resp.parsed_output
    if pkg is None:
        raise RuntimeError("Generation returned no structured output")
    pkg.content_type = content_type  # type: ignore[assignment]
    return sanitize(pkg)


def sanitize(pkg: ContentPackage) -> ContentPackage:
    """Enforce platform limits the schema can't express (Telegram quiz limits etc.)."""
    clean = []
    for q in pkg.mcqs:
        opts = [o.strip()[:100] for o in q.options if o.strip()][:10]
        if len(opts) < 2 or not 0 <= q.correct_index < len(opts):
            log.warning("dropping malformed MCQ: %s", q.question[:60])
            continue
        q.options = opts
        q.question = q.question.strip()[:300]
        q.explanation = q.explanation.strip()[:200]
        clean.append(q)
    pkg.mcqs = clean
    pkg.short.title = pkg.short.title[:95]
    pkg.short.hashtags = [h.lstrip("#").replace(" ", "") for h in pkg.short.hashtags if h.strip()][:15]
    return pkg
