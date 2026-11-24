"""The exam blueprint: how a real paper of the exam is made up.

An agent researches the latest official pattern and previous-year paper analyses once per
series: sections, questions and marks per section, negative marking, duration, and how many
questions each topic usually gets. Every test is then built to these ratios: a full mock
copies the paper exactly, a subject test keeps the subject's topic mix at its own size.
"""

from __future__ import annotations

import logging
from datetime import date

from ..config import Exam
from .models import Blueprint, Section

log = logging.getLogger(__name__)


def allocate(weights: list[float], total: int, rotate: int = 0) -> list[int]:
    """Split `total` into whole numbers proportional to `weights` (largest remainder).

    `rotate` shifts which items win ties, so consecutive small tests cover different topics."""
    n = len(weights)
    if n == 0 or total <= 0:
        return [0] * n
    w = [max(0.0, float(x)) for x in weights]
    if sum(w) <= 0:
        w = [1.0] * n
    s = sum(w)
    exact = [x * total / s for x in w]
    out = [int(e) for e in exact]
    order = sorted(range(n), key=lambda i: (-(round(exact[i] - out[i], 9)), (i - rotate) % n))
    for i in order[: total - sum(out)]:
        out[i] += 1
    return out


def normalize(bp: Blueprint) -> Blueprint:
    """Repair what an AI-made blueprint can get slightly wrong."""
    bp.sections = [s for s in bp.sections if s.subject.strip() and s.questions > 0]
    if not bp.sections:
        raise RuntimeError("The exam pattern has no sections")
    for s in bp.sections:
        s.subject = s.subject.strip()
        s.marks_per_question = s.marks_per_question if s.marks_per_question > 0 else 1
        s.negative_marks = abs(s.negative_marks)
        s.topics = [t for t in s.topics if t.topic.strip()] or [_whole(s)]
    if not 2 <= bp.options_per_question <= 5:
        bp.options_per_question = 4
    if bp.duration_minutes < 10:
        bp.duration_minutes = max(30, bp.total_questions)  # about a minute per question
    mix = [max(0, bp.difficulty_easy), max(0, bp.difficulty_medium), max(0, bp.difficulty_hard)]
    if sum(mix) == 0:
        mix = [30, 50, 20]
    bp.difficulty_easy, bp.difficulty_medium, bp.difficulty_hard = allocate(mix, 100)
    bp.pyq_years = list(dict.fromkeys(y.strip() for y in bp.pyq_years if y.strip()))
    return bp


def _whole(s: Section):
    from .models import TopicShare

    return TopicShare(topic=s.subject, questions=s.questions)


def topic_plan(section: Section, n: int, number: int = 1) -> list[tuple[str, int]]:
    """Topics and question counts for `n` questions of a section, in the paper's proportions."""
    counts = allocate([t.questions for t in section.topics], n, rotate=number - 1)
    return [(t.topic, c) for t, c in zip(section.topics, counts) if c > 0]


def difficulty_split(bp: Blueprint, n: int) -> dict[str, int]:
    e, m, h = allocate([bp.difficulty_easy, bp.difficulty_medium, bp.difficulty_hard], n)
    return {"easy": e, "medium": m, "hard": h}


def subject_size(section: Section, wanted: int) -> int:
    """Questions in a subject-wise test: as set, else the subject's share of a paper (10-50)."""
    return wanted if wanted else max(10, min(50, section.questions))


def scaled_minutes(bp: Blueprint, questions: int) -> int:
    """Time for a shorter test at the real paper's pace, rounded to 5 minutes."""
    per_q = bp.duration_minutes / max(1, bp.total_questions)
    return max(10, int(round(per_q * questions / 5.0)) * 5)


def research(exam: Exam, focus: str = "") -> Blueprint:
    from .. import llm

    prompt = f"""You are the paper-setting desk of an Indian test-series company. Today is {date.today().isoformat()}.

Exam: {exam.name} ({exam.short_name})
Audience: {exam.audience}
{('Known about the exam: ' + exam.details) if exam.details else ''}
Syllabus subjects we have: {', '.join(exam.subjects)}
{('Admin note: ' + focus) if focus else ''}

Use web search (official notification, syllabus PDF, conducting body's site, previous-year paper analyses) to find:
1. The CURRENT pattern of the main objective (MCQ) paper students practise for. If the exam has several stages or papers,
   choose the main objective one (or the one the admin note asks for) and say which.
2. Every section of that paper in order: number of questions, marks per question, negative marking, total time, options per question.
3. For each section, the topic-wise distribution: how many questions each topic/chapter usually gets, averaged over
   the last 3-5 papers (from analyses of previous-year papers). List 5-15 topics per section.
4. The difficulty mix of recent papers (easy / medium / hard %).
5. Which years' question papers or answer keys are publicly available (official site or reliable archives).
6. Recent pattern changes.

Reply with research notes in English, with numbers, and list the URLs you relied on."""
    notes = llm.web_research(prompt, 8)
    sources = "\n".join(f"- {s['title']} - {s['url']}" for s in notes.sources[:25])
    bp = llm.structured(
        "You turn research notes into the exact blueprint of one exam paper. Use the numbers the notes support; where the notes "
        "give a range, use the most recent paper. Section question counts must add up to the paper's total.",
        f"Exam: {exam.name}\n\nRESEARCH NOTES\n{notes.notes}\n\nSOURCES\n{sources}",
        Blueprint, max_tokens=16000, effort="medium")
    bp = normalize(bp)
    log.info("blueprint %s: %d sections, %d questions, %d min", exam.id, len(bp.sections), bp.total_questions, bp.duration_minutes)
    return bp
