"""The agents that write and check questions.

- writer:   writes original questions for a topic plan (topic -> count, difficulty mix) in the
            exam's style and level, in batches.
- pyq:      finds the real questions of a past paper on the web, section by section.
- checker:  solves every question independently, without the key. Disagreements (wrong key,
            two right options, missing data) are thrown out and rewritten; for previous-year
            questions the official key stays and the question is flagged for review.
- figures:  questions that need a figure get an SVG from an OpenRouter model (diagrams.py).
"""

from __future__ import annotations

import difflib
import logging
import re
import uuid
from concurrent.futures import ThreadPoolExecutor

from ..config import Exam, settings
from . import diagrams
from .blueprint import allocate
from .models import Blueprint, Question, QuestionBatch, QuestionDraft, Section, Verdicts

log = logging.getLogger(__name__)

BATCH = 15  # questions per writer call
CHECK_BATCH = 20  # questions per checker call
REFILL_ROUNDS = 2
PARALLEL = 3


def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", " ", (text or "").lower(), flags=re.UNICODE).strip()


def is_duplicate(stem: str, seen: list[str], cutoff: float = 0.92) -> bool:
    n = _norm(stem)
    if not n:
        return True
    for s in seen:
        if n == s or (abs(len(n) - len(s)) < 0.3 * max(len(n), len(s)) and difflib.SequenceMatcher(None, n, s).ratio() >= cutoff):
            return True
    return False


def valid(d: QuestionDraft, options: int) -> bool:
    opts = [o.strip() for o in d.options]
    return bool(d.question.strip()) and len(opts) == options and all(opts) and len({_norm(o) for o in opts}) == options \
        and 0 <= d.correct_index < options and bool(d.solution.strip())


def _lang(language: str) -> str:
    from ..llm import lang_rule

    return lang_rule(language)


def _system(exam: Exam, bp: Blueprint, language: str) -> str:
    return f"""You are a senior paper setter for {exam.name}. You write MCQs exactly like the real paper: same level,
style, vocabulary, length of stems and kind of distractors, as asked in recent years.
Rules:
- {_lang(language)}
- Every question has exactly {bp.options_per_question} options and exactly ONE correct option. Distractors are plausible
  (common mistakes), never silly. Spread the correct option evenly over the positions.
- Facts must be correct and current as of today; for numericals, compute the answer yourself and check it twice.
- Statement-based, match-the-following, assertion-reason and numerical formats are welcome where the real paper uses them;
  write lists/pairs inside the stem with line breaks.
- No 'all of the above' / 'none of the above' unless the real paper uses them.
- Figures: if a question truly needs one (geometry, circuit, ray diagram, graph, figure series, mirror/water image,
  Venn diagram), set needs_diagram and write a precise diagram_brief (every shape, label, value and position) that does not
  reveal the answer; the stem still gives every value needed. Otherwise needs_diagram is false and the brief empty.
- Solutions explain the reasoning step by step, the way a good teacher would.
- pyq_reference and source_url stay empty (these are new questions)."""


def write(exam: Exam, bp: Blueprint, section: Section, plan: list[tuple[str, int]], difficulty: dict[str, int],
          language: str, avoid: list[str], allow_diagrams: bool, focus: str = "") -> list[QuestionDraft]:
    """New questions for a topic plan. Returns at most the planned count per topic (fewer if the AI fell short)."""
    from .. import llm

    batches: list[list[tuple[str, int]]] = [[]]
    for topic, n in plan:  # split into calls of about BATCH questions, keeping a topic's count together where possible
        while n > 0:
            room = BATCH - sum(c for _, c in batches[-1])
            if room <= 0:
                batches.append([])
                continue
            take = min(n, room)
            batches[-1].append((topic, take))
            n -= take
    batches = [b for b in batches if b]
    total = sum(n for _, n in plan) or 1
    diff_keys = list(difficulty)

    def one(batch: list[tuple[str, int]], idx: int) -> list[QuestionDraft]:
        size = sum(c for _, c in batch)
        mix = allocate([difficulty[k] / total for k in diff_keys], size, rotate=idx)
        mix_txt = ", ".join(f"{n} {k}" for k, n in zip(diff_keys, mix) if n)
        lines = "\n".join(f"- {t}: {c} question(s)" for t, c in batch)
        avoid_txt = "\n".join(f"- {a[:140]}" for a in avoid[-60:]) or "- (none)"
        user = f"""Exam: {exam.name}
{('About the exam: ' + exam.details) if exam.details else ''}
Section: {section.subject} ({section.marks_per_question:g} marks each{', -' + format(section.negative_marks, 'g') + ' for a wrong answer' if section.negative_marks else ''})
Paper notes: {bp.notes}
{('Admin note: ' + focus) if focus else ''}

Write exactly these questions:
{lines}
Difficulty of this batch: {mix_txt}.
{'' if allow_diagrams else 'Do NOT write questions that need a figure (needs_diagram must be false).'}

Already used in this test series (write different questions):
{avoid_txt}"""
        try:
            out = llm.structured(_system(exam, bp, language), user, QuestionBatch, max_tokens=24000, effort="medium")
        except Exception:
            log.exception("question batch failed (%s)", section.subject)
            return []
        return out.questions

    with ThreadPoolExecutor(PARALLEL) as pool:
        results = list(pool.map(lambda a: one(*a), [(b, i) for i, b in enumerate(batches)]))
    want = dict(plan)
    got: dict[str, int] = {}
    seen = [_norm(a) for a in avoid]
    keep: list[QuestionDraft] = []
    topic_names = {_norm(t): t for t in want}
    for d in (q for r in results for q in r):
        t = topic_names.get(_norm(d.topic)) or _closest(d.topic, list(want))
        if t is None or got.get(t, 0) >= want[t] or not valid(d, bp.options_per_question) or is_duplicate(d.question, seen):
            continue
        d.topic = t
        if not allow_diagrams:
            d.needs_diagram, d.diagram_brief = False, ""
        got[t] = got.get(t, 0) + 1
        seen.append(_norm(d.question))
        keep.append(d)
    return keep


def _closest(topic: str, topics: list[str]) -> str | None:
    hit = difflib.get_close_matches(_norm(topic), [_norm(t) for t in topics], n=1, cutoff=0.6)
    return next((t for t in topics if _norm(t) == hit[0]), None) if hit else None


def find_pyqs(exam: Exam, bp: Blueprint, section: Section, year: str, language: str) -> list[QuestionDraft]:
    """Real questions of one section of the <year> paper, from the web. Only what could be verified."""
    from .. import llm

    research = llm.web_research(f"""Find the ACTUAL questions of the {exam.name} {year} question paper, section "{section.subject}"
({section.questions} questions in the paper).

Search for the official question paper / answer key PDF of {year} (conducting body's website first), or reputable archives
that reproduce the paper with the official answer key. Record as many questions of this section as you can find, VERBATIM:
the stem, all options in order, the OFFICIAL answer, and the URL. Note the shift/date if the exam had several.
Do not make up or "reconstruct" questions; if you cannot find the paper, say NO PAPER FOUND.
Reply with the questions as a numbered list, in the paper's language.""", settings.max_web_searches)
    if "NO PAPER FOUND" in research.notes and len(research.notes) < 400:
        return []
    srcs = "\n".join(f"- {s['title']} - {s['url']}" for s in research.sources[:20])
    out = llm.structured(
        f"""You copy real previous-year questions from research notes into structured form.
- Only questions that the notes give with all options and an answer. Never add, invent or complete questions.
- Keep the wording; translate only if the paper's language differs: {_lang(language)}
- options: exactly as in the paper (drop 'A)' prefixes); correct_index from the official answer key.
- pyq_reference: '{exam.short_name} {year}' plus the shift if known. source_url: where it was found.
- topic: the closest of these topics: {', '.join(t.topic for t in section.topics)}.
- Write a step-by-step solution for each. needs_diagram true only if the original question has a figure; then describe it
  in diagram_brief exactly as in the paper.""",
        f"Exam: {exam.name} {year}, section {section.subject}\n\nRESEARCH NOTES\n{research.notes}\n\nSOURCES\n{srcs}",
        QuestionBatch, max_tokens=32000, effort="low")
    seen: list[str] = []
    keep = []
    for d in out.questions:
        if not valid(d, len(d.options)) or len(d.options) < 2 or is_duplicate(d.question, seen):
            continue
        d.pyq_reference = d.pyq_reference.strip() or f"{exam.short_name} {year}"
        seen.append(_norm(d.question))
        keep.append(d)
    return keep[: section.questions]


def check(exam: Exam, drafts: list[QuestionDraft], language: str) -> list[str]:
    """Problems per question ('' = sound), from an independent solve without the key."""
    from .. import llm

    problems = [""] * len(drafts)

    def one(start: int) -> None:
        chunk = drafts[start:start + CHECK_BATCH]
        listing = "\n\n".join(
            f"Q{i + 1}. {d.question}\n" + "\n".join(f"  ({j}) {o}" for j, o in enumerate(d.options))
            + (f"\n  [Figure: {d.diagram_brief}]" if d.needs_diagram and d.diagram_brief else "")
            for i, d in enumerate(chunk))
        try:
            res = llm.structured(
                f"You are an expert examiner for {exam.name}. Solve each question yourself, carefully, without guessing: "
                "work numericals out fully and recall facts precisely. Give the 0-based index of the correct option and report "
                "any problem (no correct option, two correct options, ambiguity, outdated or wrong fact, missing data).",
                f"Questions (language: {language}):\n\n{listing}", Verdicts, max_tokens=16000, effort="medium")
        except Exception:
            log.exception("checker failed; batch accepted unchecked")
            return
        by_num = {v.number: v for v in res.verdicts}
        for i, d in enumerate(chunk):
            v = by_num.get(i + 1)
            if v is None:
                continue
            if v.answer_index != d.correct_index:
                problems[start + i] = f"checker chose option {v.answer_index + 1}, key says {d.correct_index + 1}. {v.problem}".strip()
            elif v.problem.strip():
                problems[start + i] = v.problem.strip()

    with ThreadPoolExecutor(PARALLEL) as pool:
        list(pool.map(one, range(0, len(drafts), CHECK_BATCH)))
    return problems


def draw_figures(drafts: list[QuestionDraft], language: str) -> list[str]:
    """SVG per draft ('' when no figure is needed or none could be drawn)."""
    import httpx

    svgs = [""] * len(drafts)
    todo = [i for i, d in enumerate(drafts) if d.needs_diagram and d.diagram_brief.strip()]
    if not todo or not diagrams.available():
        return svgs
    with httpx.Client(timeout=180) as http, ThreadPoolExecutor(4) as pool:
        for i, svg in zip(todo, pool.map(lambda i: diagrams.draw(drafts[i].diagram_brief, drafts[i].question, language, http), todo)):
            svgs[i] = svg or ""
    return svgs


def to_question(d: QuestionDraft, subject: str, svg: str = "", flagged: str = "") -> Question:
    return Question(id=uuid.uuid4().hex[:10], subject=subject, topic=d.topic, question=d.question.strip(),
                    options=[o.strip() for o in d.options], correct_index=d.correct_index, solution=d.solution.strip(),
                    difficulty=d.difficulty, svg=svg, diagram_brief=d.diagram_brief if d.needs_diagram else "",
                    pyq_reference=d.pyq_reference.strip(), source_url=d.source_url.strip(), flagged=flagged)


def build_section(exam: Exam, bp: Blueprint, section: Section, plan: list[tuple[str, int]], difficulty: dict[str, int],
                  language: str, avoid: list[str], diagrams_on: bool, focus: str = "", progress=None) -> list[Question]:
    """Write -> check -> draw figures -> rewrite what failed, until the plan is filled (or REFILL_ROUNDS run out)."""
    allow = diagrams_on and diagrams.available()
    want = dict(plan)
    done: list[Question] = []
    avoid = list(avoid)
    for rnd in range(REFILL_ROUNDS + 1):
        have: dict[str, int] = {}
        for q in done:
            have[q.topic] = have.get(q.topic, 0) + 1
        missing = [(t, n - have.get(t, 0)) for t, n in want.items() if n - have.get(t, 0) > 0]
        if not missing:
            break
        size = sum(n for _, n in missing)
        if progress:
            progress(f"{section.subject}: writing {size} question(s)" + (f" (rewrite round {rnd})" if rnd else ""))
        diff = difficulty if rnd == 0 else _scale(difficulty, size)
        drafts = write(exam, bp, section, missing, diff, language, avoid, allow and rnd < REFILL_ROUNDS, focus)
        if not drafts:
            continue
        if progress:
            progress(f"{section.subject}: checking {len(drafts)} answer(s)")
        problems = check(exam, drafts, language)
        svgs = draw_figures(drafts, language) if allow else [""] * len(drafts)
        for d, prob, svg in zip(drafts, problems, svgs):
            avoid.append(d.question)
            if prob:
                log.info("rejected (%s): %s", prob[:120], d.question[:80])
                continue
            if d.needs_diagram and not svg:
                continue  # unanswerable without its figure
            done.append(to_question(d, section.subject, svg))
    order = {t: i for i, (t, _) in enumerate(plan)}
    return sorted(done, key=lambda q: order.get(q.topic, 99))


def _scale(difficulty: dict[str, int], n: int) -> dict[str, int]:
    return dict(zip(difficulty, allocate(list(difficulty.values()), n)))


def build_pyq_section(exam: Exam, bp: Blueprint, section: Section, year: str, language: str, diagrams_on: bool,
                      progress=None) -> list[Question]:
    if progress:
        progress(f"{year} paper · {section.subject}: searching the web")
    drafts = find_pyqs(exam, bp, section, year, language)
    if not drafts:
        return []
    problems = check(exam, drafts, language)
    svgs = draw_figures(drafts, language) if diagrams_on else [""] * len(drafts)
    out = []
    for d, prob, svg in zip(drafts, problems, svgs):
        if d.needs_diagram and not svg:
            continue
        out.append(to_question(d, section.subject, svg, flagged=prob))  # official key kept; disagreement flagged
    return out
