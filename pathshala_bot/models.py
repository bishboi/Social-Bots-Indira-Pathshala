"""Structured content produced by Claude for one post.

Field descriptions double as instructions to the model (they are sent as the
JSON schema for structured output), so keep them precise.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

ContentType = Literal["pyq_quiz", "concept_explainer", "cheatsheet", "formula_sheet", "mnemonic_trick", "current_affairs"]

CONTENT_TYPES: dict[str, str] = {
    "pyq_quiz": "Previous-year-question style quiz: questions modelled on real past papers of this exam, with solutions.",
    "concept_explainer": "One concept explained simply from first principles, with an example and common mistakes.",
    "cheatsheet": "A dense one-page revision cheatsheet of the most-asked facts on the topic.",
    "formula_sheet": "Key formulas / rules / sections with when-to-use notes and a solved example.",
    "mnemonic_trick": "A memory trick or shortcut that makes a hard list or method easy to remember.",
    "current_affairs": "Recent, exam-relevant developments (last few months) connected to the static syllabus.",
}


class MCQ(BaseModel):
    question: str = Field(description="Question stem, max 280 characters, self-contained (no 'see above').")
    options: list[str] = Field(description="Exactly 4 options, each max 90 characters, no 'A)' prefixes.")
    correct_index: int = Field(description="0-based index of the single correct option.")
    explanation: str = Field(description="Why the answer is correct, max 190 characters (shown in the Telegram quiz).")
    difficulty: Literal["easy", "medium", "hard"]
    pyq_reference: str | None = Field(
        default=None,
        description="Exam + year if this is (or closely mirrors) an actual previous-year question found in research, else null. Never invent a reference.",
    )


class CheatRow(BaseModel):
    term: str = Field(description="Fact / term / formula name, short.")
    detail: str = Field(description="The fact, value or formula itself, one line.")


VIDEO_STYLES: dict[str, str] = {
    "quiz_countdown": "Quiz reel: hook -> 1-2 quiz scenes (on-screen countdown) each followed by a reveal -> short explanation -> cta.",
    "map_story": "Map explainer: hook -> 2-4 map scenes that highlight states / districts / rivers / places on an accurate map -> bullets or fact -> quiz -> reveal -> cta.",
    "timeline": "History timeline: hook -> 1-2 timeline scenes (3-6 dated events each) -> optional map or image -> quiz -> reveal -> cta.",
    "formula_breakdown": "Formula reel: hook -> 1-3 formula scenes (LaTeX + meaning of each symbol) -> bullets with a worked example -> quiz -> reveal -> cta.",
    "top_facts": "Countdown of facts: hook -> 3-5 fact scenes (one striking fact each, big number/keyword) -> cta.",
    "concept_explainer": "Concept explainer: hook -> bullets / comparison / image scenes building the idea step by step -> quiz -> reveal -> cta.",
    "slideshow": "Simple static slides: hook -> 3-5 bullets/fact scenes -> quiz -> reveal -> cta.",
}

SceneKind = Literal["title", "bullets", "fact", "quiz", "reveal", "timeline", "map", "formula", "comparison", "image", "cta"]


class MapPlace(BaseModel):
    name: str = Field(description="Official English name exactly as on Wikipedia / maps, e.g. 'Ranthambore National Park', 'Sambhar Lake', 'Kalibangan'. Coordinates are looked up from a gazetteer - never give coordinates.")
    label: str = Field(description="Short on-screen label in the content language, max 3 words.")
    kind: Literal["city", "park", "fort", "lake", "dam", "mountain", "site", "other"]


class MapSpec(BaseModel):
    focus_state: str | None = Field(default=None, description="Indian state/UT to zoom into, English name e.g. 'Rajasthan'. Null shows all of India.")
    highlight_states: list[str] = Field(default_factory=list, description="States/UTs to colour (English names).")
    highlight_districts: list[str] = Field(default_factory=list, description="Districts of focus_state to colour (English names, Census 2011 districts only).")
    rivers: list[str] = Field(default_factory=list, description="Major rivers to draw, English names e.g. 'Chambal', 'Banas', 'Mahi', 'Ganges'.")
    places: list[MapPlace] = Field(default_factory=list, description="0-6 places to pin, in the order they should appear.")


class TimelineEvent(BaseModel):
    date: str = Field(description="Date or year as it should appear, e.g. '18 Mar 1948' or '1857'.")
    label: str = Field(description="What happened, max 8 words.")


class Term(BaseModel):
    symbol: str = Field(description="Symbol as plain text, e.g. 'I_cm' or 'M'.")
    meaning: str = Field(description="Meaning, max 8 words.")


class ComparisonRow(BaseModel):
    left: str
    right: str


class Scene(BaseModel):
    kind: SceneKind
    heading: str = Field(description="On-screen heading, max 6 words.")
    body: str = Field(default="", description="Optional on-screen text, max 25 words (for quiz: the question).")
    narration: str = Field(description="Voice-over for this scene, 1-3 short spoken sentences. Spell out numbers/symbols the way a teacher would say them.")
    bullets: list[str] = Field(default_factory=list, description="bullets/fact scenes: 2-4 items, max 8 words each. For 'fact', the first item is the big number or keyword.")
    options: list[str] = Field(default_factory=list, description="quiz scenes: exactly 4 options, max 6 words each.")
    correct_index: int | None = Field(default=None, description="quiz and reveal scenes: 0-based index of the correct option.")
    events: list[TimelineEvent] = Field(default_factory=list, description="timeline scenes: 3-6 events in chronological order.")
    map: MapSpec | None = Field(default=None, description="map scenes only.")
    latex: str | None = Field(default=None, description="formula scenes: the formula in LaTeX math mode without $ delimiters, e.g. 'I = I_{cm} + Md^2'.")
    terms: list[Term] = Field(default_factory=list, description="formula scenes: meaning of each symbol (2-4).")
    left_title: str | None = Field(default=None, description="comparison scenes: left column title.")
    right_title: str | None = Field(default=None, description="comparison scenes: right column title.")
    rows: list[ComparisonRow] = Field(default_factory=list, description="comparison scenes: 2-5 rows.")
    image_topic: str | None = Field(default=None, description="image scenes: exact English Wikipedia article title whose lead photo should be shown, e.g. 'Chittor Fort'.")


class ShortVideo(BaseModel):
    style: str = Field(description="The video style requested in the prompt.")
    title: str = Field(description="YouTube Shorts title, max 80 characters, curiosity-driven, includes the exam name.")
    scenes: list[Scene] = Field(
        description="6-10 scenes for a 30-55 second vertical video following the requested style. Start with a hook ('title' kind), end with 'cta'."
    )
    caption: str = Field(description="Instagram / YouTube description, 2-4 lines, no hashtags.")
    hashtags: list[str] = Field(description="8-15 hashtags without '#', mix of exam-specific and broad education tags.")


class NoteSection(BaseModel):
    heading: str
    paragraphs: list[str] = Field(description="0-3 short paragraphs.")
    bullets: list[str] = Field(description="0-8 bullet points.")


class MnemonicCue(BaseModel):
    cue: str = Field(description="The word or phrase exactly as it appears in the trick, e.g. 'मछली'.")
    stands_for: str = Field(description="The fact it recalls, e.g. 'मत्स्य संघ (18 मार्च 1948)'.")


class Mnemonic(BaseModel):
    kind: Literal["story", "sentence", "rhyme", "acronym_word", "visual", "number_link", "association"] = Field(
        description="story/sentence: a short meaningful sentence whose words hint at each item in order; rhyme: a rhyming line; "
        "acronym_word: the initials form a REAL, pronounceable word; visual: a vivid mental picture; number_link: ties a number/date "
        "to something familiar; association: links one confusing fact to something already known."
    )
    trick: str = Field(description="The trick itself, max 140 characters, in the content language. Must read naturally and be easy to say aloud.")
    decode: list[MnemonicCue] = Field(description="Every cue in the trick, in order, with exactly what it stands for. Each cue must appear verbatim in 'trick'.")
    tip: str = Field(description="One line on how to use it in the exam, e.g. which question type it answers.")


class Source(BaseModel):
    title: str
    url: str


class ContentPackage(BaseModel):
    title: str = Field(description="Title of this lesson, max 70 characters.")
    subject: str
    topic: str = Field(description="The specific sub-topic actually covered.")
    content_type: ContentType
    hook: str = Field(description="One-line attention grabber for the Telegram post.")
    telegram_post: str = Field(
        description="Telegram channel post body (max 900 characters) using only <b>, <i>, <u>, <code> HTML tags and line breaks; emoji allowed; summarises the key learning."
    )
    key_points: list[str] = Field(description="5-8 crisp key takeaways.")
    cheatsheet: list[CheatRow] = Field(description="8-16 rows of high-yield facts for quick revision.")
    mnemonic: Mnemonic | None = Field(
        default=None,
        description="A memory trick ONLY if the topic has a list, sequence, set of dates/numbers or an easily-confused pair worth memorising; else null.",
    )
    mcqs: list[MCQ] = Field(description="3-5 exam-level multiple choice questions of mixed difficulty.")
    short: ShortVideo
    notes: list[NoteSection] = Field(description="4-7 sections of printable revision notes covering the topic in depth.")
    sources: list[Source] = Field(description="Web sources actually used during research (from the research notes).")

    @field_validator("mnemonic", mode="before")
    @classmethod
    def _accept_legacy_string(cls, v):
        return _legacy_mnemonic(v)


def _legacy_mnemonic(v):
    """Older content.json files stored the trick as a plain string."""
    if isinstance(v, str):
        return {"kind": "sentence", "trick": v, "decode": [], "tip": ""} if v.strip() else None
    return v




def mnemonic_issues(m: Mnemonic | None) -> list[str]:
    """Mechanical checks a good trick must pass (the review step fixes what fails)."""
    if m is None:
        return []
    problems = []
    trick = m.trick.strip()
    if len(trick) > 160:
        problems.append("trick is too long to say in one breath")
    if not m.decode:
        problems.append("no decode: the learner cannot tell what each part stands for")
    if len(m.decode) == 1 and m.kind in ("story", "sentence", "acronym_word"):
        problems.append("only one cue - a list trick needs one cue per item")
    missing = [c.cue for c in m.decode if c.cue.strip() and c.cue.strip() not in trick]
    if missing:
        problems.append(f"cues not found in the trick text: {missing}")
    # letter soup like "म-पू-सं-वृ" or "M-P-S-V": mostly 1-2 character chunks
    import re

    for run in re.findall(r"(?:[^\s\-–]+\s*[-–]\s*){3,}[^\s\-–]+", trick):
        parts = [x.strip(' "\'') for x in re.split(r"[-–]", run)]
        if sum(len(x) <= 3 for x in parts) >= len(parts) * 0.6:
            problems.append("trick is a string of letters, not words a person can remember")
            break
    return problems
