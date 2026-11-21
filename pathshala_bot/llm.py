"""AI calls: web research, then structured content generation.

Provider-neutral: the actual API calls live in providers.py (Anthropic or OpenAI,
selected with LLM_PROVIDER or in the admin panel).
"""

from __future__ import annotations

import logging
from datetime import date

from .config import Exam, settings
from typing import Literal

from pydantic import BaseModel, Field

from .models import CONTENT_TYPES, VIDEO_STYLES, ContentPackage, Mnemonic, mnemonic_issues

log = logging.getLogger(__name__)

from . import providers
from .providers import ContentRefused, Research  # noqa: F401  (re-exported)

LANG_RULES = {
    "hi": "Write ALL learner-facing text in Hindi (Devanagari script). Keep standard technical terms, formulas, section numbers and proper nouns as commonly written in Hindi-medium exam material.",
    "en": "Write all learner-facing text in clear Indian English.",
    "hinglish": "Write in simple Hinglish (Hindi in Roman script mixed with English terms), the way popular Indian educators talk. Keep formulas and technical terms in English.",
}


MNEMONIC_RULES = """Memory tricks ("याद रखने की ट्रिक") - only when there is a list, sequence, set of dates/numbers or a confusing pair to memorise:
- Make a short sentence, story, rhyme or real word that a student can say aloud and picture. It must make sense on its own.
- Every cue must be a whole, familiar word that *hints at* its item by sound or meaning (e.g. 'मछली' for मत्स्य, because मत्स्य means fish) - not a bare initial letter.
- One cue per item, in the same order as the facts, and no extra cues. Every cue appears verbatim in the trick.
- Prefer funny, vivid or local images (chai, cricket, train, family) - absurd is fine, confusing is not.
- Keep it under ~15 words. Fill 'decode' so a student can check each cue against its fact.
- Never invent or change a fact to make the trick fit.
GOOD: "मछली पूरब से संग चली, बड़ी होकर संग-बड़ी बनी, सिरोही होते अजमेर पहुँची" -> मछली = मत्स्य संघ, पूरब = पूर्व राजस्थान, संग = संयुक्त राजस्थान, बड़ी = वृहत् राजस्थान, संग-बड़ी = संयुक्त वृहत् राजस्थान, सिरोही = सिरोही का विलय, अजमेर = अजमेर-मेरवाड़ा (1956).
GOOD: "OIL RIG" -> Oxidation Is Loss (of electrons), Reduction Is Gain.
BAD: "म-पू-सं-वृ-मत्स्य-सि-अ" (letter soup - nothing to picture). BAD: "Remember PV = nRT" (restates the fact, no trick)."""


class MnemonicReview(BaseModel):
    verdict: Literal["keep", "rewrite", "drop"]
    score: int = Field(description="Memorability of the ORIGINAL draft, 1-10, judged as a student hearing it once.")
    problems: list[str] = Field(description="Concrete problems with the draft (empty if none).")
    improved: Mnemonic | None = Field(default=None, description="The final trick for 'keep' (unchanged) or 'rewrite' (new); null for 'drop'.")


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

    return web_research(prompt, settings.max_web_searches)


def generate_package(exam: Exam, subject: str, content_type: str, research: Research, video_style: str = "concept_explainer") -> ContentPackage:
    """Turn research notes into every asset (quiz, short script, notes...) in one structured call."""
    src_list = "\n".join(f"- {s['title']} - {s['url']}" for s in research.sources[:25])
    system = f"""You are the head teacher and content writer of "{settings.brand_name}" ({settings.brand_handle}), an Indian exam-prep brand that posts on Telegram, Instagram Reels and YouTube Shorts.

Standards:
- Accuracy first: use only facts supported by the research notes. If something is uncertain, leave it out.
- Exam-oriented: match the level, style and vocabulary of {exam.name}.
- {LANG_RULES.get(exam.language, LANG_RULES['en'])}
- MCQs must have exactly 4 options and exactly one correct answer; distractors should be plausible. Vary the position of the correct option.
- The short video is animated scene by scene. Each scene is on screen for 3-7 seconds: very few words on screen, the narration carries the explanation.
- Map scenes: only name real places/districts/rivers with their official English names; coordinates and boundaries come from verified datasets, never from you. Only use map scenes when location genuinely matters.
- Image scenes: name an English Wikipedia article whose lead photo shows the subject (a fort, monument, person, landform). Only use when a real photo adds value.
- Formula scenes: valid LaTeX math (no $), symbols explained in 'terms'.
- Never claim a question is a previous-year question unless the research notes show it is.

{MNEMONIC_RULES}"""

    user = f"""Exam: {exam.name} ({exam.short_name})
Audience: {exam.audience}
Subject: {subject}
Format: {content_type} - {CONTENT_TYPES[content_type]}
Video style: {video_style} - {VIDEO_STYLES.get(video_style, "")}

RESEARCH NOTES
{research.notes}

SOURCES FOUND
{src_list or '- none'}

Create the complete content package for this topic."""

    pkg = structured(system, user, ContentPackage, max_tokens=32000)
    pkg.content_type = content_type  # type: ignore[assignment]
    pkg.short.style = video_style
    if settings.mnemonic_review and (pkg.mnemonic is not None or content_type == "mnemonic_trick"):
        try:
            pkg.mnemonic = review_mnemonic(exam, pkg)
        except Exception:
            log.exception("mnemonic review failed; keeping the draft only if it passes the checks")
            if mnemonic_issues(pkg.mnemonic):
                pkg.mnemonic = None
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
    for sc in pkg.short.scenes:
        sc.options = [o.strip() for o in sc.options if o.strip()][:4]
        if sc.correct_index is not None and not 0 <= sc.correct_index < max(1, len(sc.options)):
            sc.correct_index = None
        if sc.kind == "map" and sc.map is None:
            sc.kind = "bullets"  # type: ignore[assignment]
    pkg.short.hashtags = [h.lstrip("#").replace(" ", "") for h in pkg.short.hashtags if h.strip()][:15]
    return pkg


def review_mnemonic(exam: Exam, pkg: ContentPackage) -> Mnemonic | None:
    """Second opinion on the memory trick: keep it, rewrite it, or drop it."""
    facts = "\n".join([f"- {p}" for p in pkg.key_points] + [f"- {r.term}: {r.detail}" for r in pkg.cheatsheet])
    draft = pkg.mnemonic.model_dump_json(indent=1) if pkg.mnemonic else "(none - create one if the facts contain something worth memorising)"
    issues = mnemonic_issues(pkg.mnemonic)
    prompt = f"""You review memory tricks for {exam.name} students. {LANG_RULES.get(exam.language, LANG_RULES['en'])}

TOPIC: {pkg.topic}
FACTS (the only source of truth):
{facts}

DRAFT TRICK:
{draft}

AUTOMATIC CHECKS FAILED: {issues or 'none'}

{MNEMONIC_RULES}

Test the draft like a student who hears it once: Can you say it aloud easily? Does it make sense as a sentence/picture?
Does each cue clearly point to its fact, in order, with nothing missing or extra? Is every decoded fact exactly right per FACTS?
- 'keep' only if it scores 8+ and passes every check (return it unchanged in 'improved').
- Otherwise 'rewrite': think of three different tricks and return the best one in 'improved'.
- 'drop' if the facts have nothing worth a trick (no list/sequence/numbers/confusable pair)."""
    review = structured("You review memory tricks for exam students.", prompt, MnemonicReview, max_tokens=8000)
    log.info("mnemonic review: %s (draft score %s) %s", review.verdict, review.score, review.problems)
    final = review.improved if review.verdict != "drop" else None
    if final is not None and mnemonic_issues(final):
        log.warning("reviewed trick still fails checks %s - dropping it", mnemonic_issues(final))
        return None
    return final

def web_research(prompt: str, max_searches: int) -> Research:
    """Web-search research turn with cited sources (current provider)."""
    return providers.get().web_research(prompt, max_searches)


def structured(system: str, user: str, model, max_tokens: int = 32000, effort: str | None = None):
    """One structured-output call returning a validated pydantic object (current provider)."""
    return providers.get().structured(system, user, model, max_tokens=max_tokens, effort=effort)
