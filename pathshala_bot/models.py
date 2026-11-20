"""Structured content produced by Claude for one post.

Field descriptions double as instructions to the model (they are sent as the
JSON schema for structured output), so keep them precise.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

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


class Slide(BaseModel):
    kind: Literal["hook", "concept", "question", "answer", "tip", "cta"]
    heading: str = Field(description="Big on-screen heading, max 6 words.")
    body: str = Field(description="On-screen text, max 30 words. For 'question' slides, the question with options on separate lines 'A. ...'.")
    narration: str = Field(description="What the voice-over says while this slide is shown, 1-3 short spoken sentences.")


class ShortVideo(BaseModel):
    title: str = Field(description="YouTube Shorts title, max 80 characters, curiosity-driven, includes the exam name.")
    slides: list[Slide] = Field(
        description="6-9 slides for a 30-55 second vertical video: start with a hook, end with a call-to-action to follow / join Telegram."
    )
    caption: str = Field(description="Instagram / YouTube description, 2-4 lines, no hashtags.")
    hashtags: list[str] = Field(description="8-15 hashtags without '#', mix of exam-specific and broad education tags.")


class NoteSection(BaseModel):
    heading: str
    paragraphs: list[str] = Field(description="0-3 short paragraphs.")
    bullets: list[str] = Field(description="0-8 bullet points.")


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
    mnemonic: str | None = Field(default=None, description="A memory trick if one genuinely helps, else null.")
    mcqs: list[MCQ] = Field(description="3-5 exam-level multiple choice questions of mixed difficulty.")
    short: ShortVideo
    notes: list[NoteSection] = Field(description="4-7 sections of printable revision notes covering the topic in depth.")
    sources: list[Source] = Field(description="Web sources actually used during research (from the research notes).")
