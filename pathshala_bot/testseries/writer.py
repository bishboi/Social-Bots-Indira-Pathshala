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
from .models import Blueprint, PaperQuestions, Question, QuestionBatch, QuestionDraft, Section, Verdicts

log = logging.getLogger(__name__)

BATCH = 10  # questions per writer call (each carries a hint and a detailed explanation)
CHECK_BATCH = 20  # questions per checker call
REFILL_ROUNDS = 4
OVERWRITE = 1.4  # rewrite rounds ask for this many times the missing count (some are rejected again)
PARALLEL = 4


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
- Every question carries three layers of help, in the content language:
  hint: a nudge to think in the right direction (the concept, rule, formula or first step) - never the answer, never
        "option B", and it must not rule options out by name;
  solution: the answer with the key steps, short;
  explanation: a detailed lesson a weak student can learn from - the concept behind the question, the full working,
        why EACH wrong option is wrong, and one related fact, shortcut or common trap worth remembering.
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
            out = llm.structured(_system(exam, bp, language), user, QuestionBatch, max_tokens=32000, effort="medium")
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


COMPLETE = 0.95  # a past paper needs this share of its questions (the rest: questions the official key deleted)
MAX_KEY_DISAGREEMENT = 0.55  # a wrong-booklet/shift key disagrees ~randomly (>55%); a correct key on a hard subject disagrees less, and those are flagged


def _sitting(text: str) -> str:
    """'13-09-2021' from a date in any common numeric format ('' if none)."""
    m = re.search(r"(\d{1,2})[-./ ](\d{1,2})[-./ ](\d{4})", text or "")
    return f"{int(m.group(1)):02d}-{int(m.group(2)):02d}-{m.group(3)}" if m else ""


def find_pyqs(exam: Exam, bp: Blueprint, section: Section, year: str, language: str,
              prefer_sitting: str = "") -> tuple[list[QuestionDraft], str, str, str]:
    """A section of a real <year> paper, from ONE document, with that paper's official answers.

    Returns (questions in paper order, sitting date, source URL, note). The most complete copy whose answers belong
    to its questions wins; it may be incomplete (note says why) - the caller fills the gap with labelled practice
    questions. Nothing usable: ([], "", "", reason). prefer_sitting: date of the other paper(s) of this test."""
    from .. import llm
    from . import sources as web

    research = llm.web_research(f"""Find where the ACTUAL questions of the {exam.name} {year} question paper, section/paper
"{section.subject}", are published COMPLETE ({section.questions} questions) with the OFFICIAL answers: the conducting body's
question paper and answer key PDFs first, then reputable sites that reproduce the whole paper with the official answer key
(e.g. "{exam.short_name} {year} question paper with answer key", "{year} solved paper").
{('Prefer the paper of the sitting held on ' + prefer_sitting + '.') if prefer_sitting else ''}
List every such URL on its own line under "URLS:" (direct PDF links are best), most complete first, and say for each what
it contains and the exam date/shift. If the paper of {year} was never held or isn't published anywhere, say NO PAPER FOUND.""",
                                settings.max_web_searches)
    # a second search in the paper's own terms finds copies the first one misses (Hindi coaching sites, PDF archives)
    research2 = llm.web_research(f"""Search for the complete {exam.short_name} {year} "{section.subject}" question paper with its answer key,
in Hindi and English (e.g. "{exam.short_name} {year} प्रश्न पत्र उत्तर कुंजी सहित", "{exam.short_name} {year} paper pdf with answer key",
"{exam.short_name} {year} solved paper"), including direct PDF links and pages that show all questions with answers.
List each URL on its own line under "URLS:" with what it contains (complete or partial, date/shift).""", settings.max_web_searches)
    named = re.findall(r"https?://[^\s<>\"')\]]+", research.notes + "\n" + research2.notes)  # URLs research names as the paper come first
    urls = named + [s["url"] for s in research.sources + research2.sources]
    found = web.gather(urls, limit=8, attempts=30, keyword=f"{section.subject} {year}")
    topics = ", ".join(t.topic for t in section.topics)
    rules = f"""- Only questions of the section/paper "{section.subject}" of {exam.name} {year}. If the material belongs to another
  exam, year or paper, return an empty list. Never add, invent, complete or re-word questions.
- number: the question number as printed.
- Keep the wording; translate only if the paper's language differs: {_lang(language)}
- options: only the real answer options, exactly as printed, without numbering. Leave out an extra option meaning
  "unattempted / not answered" (e.g. 'अनुत्तरित प्रश्न').
- topic: the closest of: {topics}.
- pyq_reference '{exam.short_name} {year}'; sitting: the paper's date (and shift) as printed, DD-MM-YYYY.
- needs_diagram only if the question has a figure; then describe the figure exactly in diagram_brief."""
    keys = "\n\n".join(f"ANSWER KEY ({u}):\n{t[:12000]}" for u, t in found.keys[:3])

    def extract(chunk: tuple[str, str]):
        url, text = chunk
        try:
            return llm.structured(
                f"""You copy real previous-year questions from a document into structured form.
{rules}
- Only questions the document gives WITH an answer (answer key, marked answer or "Ans:"); correct_index from that answer.
- Skip a question cut off at the start or end of the excerpt.
- source_url: {url}""",
                f"DOCUMENT ({url}):\n{text}", PaperQuestions, max_tokens=24000, effort="low").questions
        except Exception:
            log.exception("PYQ extraction failed for %s", url)
            return []

    def ocr(job: tuple[str, list[bytes], int]):
        url, pages, first = job
        try:
            return llm.structured(
                f"""You transcribe a scanned question paper (page images) into structured form, accurately, character by character.
{rules}
- Ignore pen marks, ticks and notes made by a candidate: they are NOT answers.
- correct_index comes ONLY from the official answer key below, by question number (keys number options from 1, so
  correct_index = key value - 1). Use only the key of this same paper (subject, date/shift). Skip a question if the key
  has no usable answer for it (deleted / cancelled / missing).
- Questions cut off at the page edge: skip them.
- source_url: {url}""",
                f"Pages {first + 1}-{first + len(pages)} of the paper.\n\n{keys}", PaperQuestions,
                max_tokens=24000, effort="low", images=pages).questions
        except Exception:
            log.exception("OCR failed for %s pages %d+", url, first + 1)
            return []

    # each candidate document on its own: a paper is never pieced together from several sources
    candidates: dict[str, list] = {}
    chunks: list[tuple[str, str]] = []
    for url, text in found.papers:  # read each source fully, in overlapping excerpts so no question is lost at a boundary
        step, size = 12000, 15000
        chunks += [(url, text[i:i + size]) for i in range(0, max(1, len(text) - (size - step)), step)][:10]
    chunks = chunks[:36]
    with ThreadPoolExecutor(PARALLEL) as pool:
        for chunk, qs in zip(chunks, pool.map(extract, chunks)):
            for q in qs:
                q.source_url = chunk[0]
                candidates.setdefault(chunk[0], []).append(q)
    for url, pdf in (found.scanned[:4] if found.keys else []):
        pages = web.page_images(pdf)
        jobs = [(url, pages[i:i + 3], i) for i in range(0, len(pages), 3)]
        with ThreadPoolExecutor(PARALLEL) as pool:
            got = [q for qs in pool.map(ocr, jobs) for q in qs]
        log.info("OCR of %s: %d page(s) -> %d question(s)", url, len(pages), len(got))
        for q in got:
            q.source_url = url
            candidates.setdefault(url, []).append(q)

    need = int(section.questions * COMPLETE + 0.999)
    reason = "no copy of this paper with its answers was found"
    ranked = []
    for url, qs in candidates.items():
        qs = _paper_order(qs)
        sitting = next((d for d in (_sitting(q.sitting) or _sitting(q.pyq_reference) for q in qs) if d), "")
        ranked.append((len(qs) >= need, bool(prefer_sitting) and sitting == prefer_sitting, len(qs), url, qs, sitting))
    ranked.sort(key=lambda r: (r[0], r[1], r[2]), reverse=True)
    for complete, _, n, url, qs, sitting in ranked:
        if not trusted(exam, qs, language):  # answers from another booklet / shift / paper
            reason = f"the answers in {url} don't belong to its questions"
            continue
        qs = qs[: section.questions]
        helps = explain(exam, qs, language)
        ref = f"{exam.short_name} {year}" + (f" ({sitting})" if sitting else "")
        out = [QuestionDraft(topic=q.topic, question=q.question.strip(), options=[o.strip() for o in q.options],
                             correct_index=q.correct_index, hint=h[0], solution=h[1], explanation=h[2], difficulty="medium",
                             needs_diagram=q.needs_diagram, diagram_brief=q.diagram_brief, pyq_reference=ref, source_url=url)
               for q, h in zip(qs, helps)]
        if any(not d.solution for d in out):  # retry explanations once for the few that failed
            missing = [i for i, d in enumerate(out) if not d.solution]
            for i, h in zip(missing, explain(exam, [qs[i] for i in missing], language)):
                out[i].hint, out[i].solution, out[i].explanation = h
        out = [d for d in out if d.solution]
        note = "" if len(out) >= need else f"the most complete copy found has {len(out)} of {section.questions} questions"
        return out, sitting, url, note
    return [], "", "", reason


def _paper_order(qs: list) -> list:
    """One document's questions in paper order, each once (by number; a repeated stem with other options is kept)."""
    out, seen = [], set()
    for q in sorted(qs, key=lambda q: q.number or 10**6):
        opts = [o.strip() for o in q.options]
        if len(opts) < 2 or not all(opts) or not 0 <= q.correct_index < len(opts):
            continue
        key = q.number if q.number else _norm(q.question + "|" + "|".join(opts))
        if key in seen:
            continue
        seen.add(key)
        out.append(q)
    return out


def trusted(exam: Exam, questions: list, language: str, sample: int = 20) -> bool:
    """Do these answers belong to these questions? An independent solve of a sample must mostly agree.

    Many disagreements (over half) mean the answers came from another booklet, shift or paper and the whole copy is
    unusable. Fewer are normal - hard or disputed questions (especially Hindi grammar) where the official key stands
    and each disagreement is flagged for review."""
    part = questions[:sample]
    if len(part) < 5:
        return True
    drafts = [QuestionDraft(topic=q.topic, question=q.question, options=q.options, correct_index=q.correct_index, hint="",
                            solution="", explanation="", difficulty="medium", needs_diagram=q.needs_diagram,
                            diagram_brief=q.diagram_brief, pyq_reference="", source_url=q.source_url) for q in part]
    wrong = sum(1 for p in check(exam, drafts, language) if p.startswith("checker chose"))
    if wrong / len(part) > MAX_KEY_DISAGREEMENT:
        log.warning("answers of %s don't match its questions: %d of %d disagree with an independent solve",
                    part[0].source_url, wrong, len(part))
        return False
    return True


def explain(exam: Exam, questions, language: str) -> list[tuple[str, str, str]]:
    """(hint, solution, explanation) for each question, written in batches."""
    from .. import llm
    from .models import HelpBatch

    out: list[tuple[str, str, str]] = [("", "", "")] * len(questions)

    def one(start: int) -> None:
        chunk = questions[start:start + BATCH]
        listing = "\n\n".join(f"Q{i + 1}. {q.question}\n" + "\n".join(f"  ({j}) {o}" for j, o in enumerate(q.options))
                               + f"\n  Correct (official key): ({q.correct_index}) {q.options[q.correct_index]}" for i, q in enumerate(chunk))
        try:
            res = llm.structured(
                f"""You are a senior teacher for {exam.name}. For each question write, in the content language:
- hint: a nudge towards the concept, rule or first step - never the answer, never an option letter;
- solution: the answer with the key steps, short;
- explanation: a detailed lesson - the concept, full working, why EACH wrong option is wrong, and a related fact,
  shortcut or common trap. Explain the official answer.
{_lang(language)}""", listing, HelpBatch, max_tokens=32000, effort="medium")
        except Exception:
            log.exception("explanations failed for a batch")
            return
        for h in res.items:
            if 1 <= h.number <= len(chunk):
                out[start + h.number - 1] = (h.hint.strip(), h.solution.strip(), h.explanation.strip())

    with ThreadPoolExecutor(PARALLEL) as pool:
        list(pool.map(one, range(0, len(questions), BATCH)))
    return out


def check(exam: Exam, drafts: list[QuestionDraft], language: str) -> list[str]:
    """Problems per question ('' = sound), from an independent solve without the key.

    A hint that gives the answer away is rewritten in place (the question itself is fine)."""
    from .. import llm

    problems = [""] * len(drafts)
    weak_hints: list[int] = []

    def one(start: int) -> None:
        chunk = drafts[start:start + CHECK_BATCH]
        listing = "\n\n".join(
            f"Q{i + 1}. {d.question}\n" + "\n".join(f"  ({j}) {o}" for j, o in enumerate(d.options))
            + (f"\n  [Figure: {d.diagram_brief}]" if d.needs_diagram and d.diagram_brief else "")
            + (f"\n  Hint shown to students: {d.hint}" if d.hint.strip() else "")
            for i, d in enumerate(chunk))
        try:
            res = llm.structured(
                f"You are an expert examiner for {exam.name}. Solve each question yourself, carefully, without guessing: "
                "work numericals out fully and recall facts precisely. Give the 0-based index of the correct option. In 'problem' "
                "report only real defects (wrong key, no correct option, two defensible correct options, outdated or wrong fact, "
                "missing data) - leave it empty for a sound question, even if the wording could be nicer. Judge the hint separately "
                "in 'hint_reveals'.",
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
            elif v.hint_reveals:
                weak_hints.append(start + i)

    with ThreadPoolExecutor(PARALLEL) as pool:
        list(pool.map(one, range(0, len(drafts), CHECK_BATCH)))
    if weak_hints:
        rewrite_hints(exam, [drafts[i] for i in weak_hints], language)
    return problems


def rewrite_hints(exam: Exam, drafts: list[QuestionDraft], language: str) -> None:
    """New hints for questions whose hint gave the answer away (no hint is better than a spoiler)."""
    from .. import llm
    from .models import HintBatch

    listing = "\n\n".join(f"Q{i + 1}. {d.question}\n" + "\n".join(f"  ({j}) {o}" for j, o in enumerate(d.options))
                           + f"\n  Answer: ({d.correct_index}). Old hint (too revealing): {d.hint}" for i, d in enumerate(drafts))
    new: dict[int, str] = {}
    try:
        res = llm.structured(f"You write hints for {exam.name} practice questions. A hint points to the concept, rule or first "
                             f"step to think of; it never gives the answer, names an option or rules options out. {_lang(language)}",
                             listing, HintBatch, max_tokens=8000, effort="low")
        new = {h.number: h.hint.strip() for h in res.items}
    except Exception:
        log.exception("hint rewrite failed")
    for i, d in enumerate(drafts):
        d.hint = new.get(i + 1, "")


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
                    hint=d.hint.strip(), explanation=d.explanation.strip(),
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
        if rnd:
            import math

            missing = [(t, math.ceil(n * OVERWRITE)) for t, n in missing]
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
            if sum(1 for q in done if q.topic == d.topic) >= want.get(d.topic, 0):
                continue  # extra from an over-asked rewrite round
            done.append(to_question(d, section.subject, svg))
    order = {t: i for i, (t, _) in enumerate(plan)}
    return sorted(done, key=lambda q: order.get(q.topic, 99))


def _scale(difficulty: dict[str, int], n: int) -> dict[str, int]:
    return dict(zip(difficulty, allocate(list(difficulty.values()), n)))


def build_pyq_section(exam: Exam, bp: Blueprint, section: Section, year: str, language: str, diagrams_on: bool,
                      progress=None, prefer_sitting: str = "", focus: str = "") -> tuple[list[Question], str, str, int]:
    """A section of a real past paper: (questions, sitting date, note, real count).

    Real questions keep their official answers (a checker disagreement is flagged for review). Questions that couldn't
    be found are written in the paper's pattern and marked practice=True - never passed off as the real paper."""
    if progress:
        progress(f"{year} paper · {section.subject}: searching the web")
    drafts, sitting, source, note = find_pyqs(exam, bp, section, year, language, prefer_sitting)
    problems = check(exam, drafts, language) if drafts else []
    svgs = draw_figures(drafts, language) if diagrams_on and drafts else [""] * len(drafts)
    real = []
    for d, prob, svg in zip(drafts, problems, svgs):
        if d.needs_diagram and not svg:
            continue  # its figure couldn't be drawn: a practice question takes its place
        real.append(to_question(d, section.subject, svg, flagged=prob))
    real = real[: section.questions]
    missing = section.questions - len(real)
    practice: list[Question] = []
    if missing > 0:
        if progress:
            progress(f"{year} paper · {section.subject}: {len(real)} real question(s) found, writing {missing} practice question(s)")
        from .blueprint import difficulty_split, topic_plan

        have: dict[str, int] = {}
        for q in real:
            have[q.topic] = have.get(q.topic, 0) + 1
        plan = [(t, n - have.get(t, 0)) for t, n in topic_plan(section, section.questions) if n - have.get(t, 0) > 0]
        if sum(n for _, n in plan) < missing:
            plan = topic_plan(section, missing)
        practice = build_section(exam, bp, section, plan, difficulty_split(bp, missing), language,
                                 [q.question for q in real], diagrams_on, focus, progress)[:missing]
        for q in practice:
            q.practice = True
    reason = note or ("" if real else "the paper could not be found")
    return real + practice, sitting, reason, len(real)
