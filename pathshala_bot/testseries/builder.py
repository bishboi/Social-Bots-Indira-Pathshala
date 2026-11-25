"""The test-series agent: plans the funnel, builds each test and releases it.

Funnel, in order:
  1. Previous-year papers  - the real papers of recent years (free: posted with PDFs, pulls students in)
  2. Subject-wise tests    - one subject at a time, in that subject's topic proportions
  3. Full-length mocks     - exactly like the real paper: sections, counts, marks, time
Each stage has an access level: free (posted + open), signup (name + phone to attempt, so
every attempt is a lead) or private (admin only).

Files: <OUTPUT_DIR>/testseries/<series>/blueprint.json and <test>/test.json, paper.pdf, solutions.pdf
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .. import control
from ..config import Exam, settings
from . import blueprint as bpmod
from . import writer
from .models import KINDS, Blueprint, Test, TestSection, TestSpec

log = logging.getLogger(__name__)


class NoPaperFound(RuntimeError):
    pass


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:30].strip("-")
    return s or "s"


def root() -> Path:
    return settings.output_dir / "testseries"


def series_dir(series_id: str) -> Path:
    return root() / series_id


def test_dir(series_id: str, test_id: str) -> Path:
    return series_dir(series_id) / test_id


# ---------------------------------------------------------------- blueprint

def load_blueprint(series: control.TestSeries) -> Blueprint | None:
    p = series_dir(series.id) / "blueprint.json"
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    if data.get("exam_id") != series.exam_id:
        return None  # the series now points to another exam
    return Blueprint.model_validate(data["blueprint"])


def save_blueprint(series: control.TestSeries, bp: Blueprint) -> None:
    p = series_dir(series.id) / "blueprint.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"exam_id": series.exam_id, "researched_on": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                             "blueprint": bp.model_dump()}, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_blueprint(cfg: control.ControlConfig, series: control.TestSeries, refresh: bool = False) -> Blueprint:
    bp = None if refresh else load_blueprint(series)
    if bp is None:
        bp = bpmod.research(exam_for(cfg, series), series.focus)
        save_blueprint(series, bp)
    return bp


def exam_for(cfg: control.ControlConfig, series: control.TestSeries) -> Exam:
    defs = control.all_exam_defs(cfg)
    if series.exam_id not in defs:
        raise RuntimeError(f"exam '{series.exam_id}' not found")
    exam = defs[series.exam_id]
    return replace(exam, language=series.language) if series.language else exam


# ---------------------------------------------------------------- plan

def plan(series: control.TestSeries, bp: Blueprint) -> list[TestSpec]:
    specs: list[TestSpec] = []
    order = 0
    for stage in series.stages:
        if not stage.enabled or stage.count <= 0:
            continue
        if stage.kind == "pyq":
            for year in bp.pyq_years[: stage.count]:
                order += 1
                specs.append(TestSpec(id=f"pyq-{slug(year)}", kind="pyq", order=order, title=f"{bp.exam_name} · {year}",
                                      year=year, questions=bp.total_questions, access=stage.access))
        elif stage.kind == "subject":
            for n in range(1, stage.count + 1):
                for sec in bp.sections:
                    order += 1
                    size = bpmod.subject_size(sec, stage.questions)
                    specs.append(TestSpec(id=f"subject-{slug(sec.subject)}-{n}", kind="subject", order=order,
                                          title=f"{sec.subject} · Test {n}", subject=sec.subject, number=n,
                                          questions=size, access=stage.access))
        else:
            for n in range(1, stage.count + 1):
                order += 1
                specs.append(TestSpec(id=f"full-{n}", kind="full", order=order, title=f"Full Mock Test {n}", number=n,
                                      questions=bp.total_questions, access=stage.access))
    return specs


def built(series_id: str) -> dict[str, Test]:
    out = {}
    base = series_dir(series_id)
    for p in sorted(base.glob("*/test.json")) if base.exists() else []:
        try:
            t = Test.model_validate_json(p.read_text(encoding="utf-8"))
            out[t.id] = t
        except Exception:
            log.exception("unreadable test %s", p)
    return out


def load_test(series_id: str, test_id: str) -> Test | None:
    if not re.match(r"^[a-z0-9-]{1,60}$", test_id or "") or not re.match(r"^[a-z0-9-]{1,40}$", series_id or ""):
        return None
    p = test_dir(series_id, test_id) / "test.json"
    return Test.model_validate_json(p.read_text(encoding="utf-8")) if p.exists() else None


def save_test(test: Test) -> Path:
    d = test_dir(test.series_id, test.id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "test.json").write_text(test.model_dump_json(indent=1), encoding="utf-8")
    return d


def delete_test(series_id: str, test_id: str) -> bool:
    d = test_dir(series_id, test_id)
    if load_test(series_id, test_id) is None and not (d / "skipped.json").exists():
        return False
    shutil.rmtree(d)
    return True


def used_stems(series_id: str, subject: str) -> list[str]:
    """Questions already in this series for a subject (so new tests don't repeat them)."""
    return [q.question for t in built(series_id).values() for q in t.questions if q.subject == subject]


# ---------------------------------------------------------------- build

def build_test(cfg: control.ControlConfig, series: control.TestSeries, spec: TestSpec, bp: Blueprint,
               progress=None) -> Test:
    exam = exam_for(cfg, series)
    lang = exam.language
    sections: list[TestSection] = []
    questions = []
    note = ""
    if spec.kind == "pyq":
        # a real paper: every section complete, from one copy each, with the official answers - or no test at all
        sittings, sources = [], []
        for sec in bp.sections:
            try:
                qs, sitting, src = writer.build_pyq_section(exam, bp, sec, spec.year, lang, series.diagrams, progress,
                                                            prefer_sitting=sittings[0] if sittings else "")
            except writer.PaperNotFound as e:
                raise NoPaperFound(f"{spec.year} paper not published: {e}") from e
            sections.append(TestSection(subject=sec.subject, marks_per_question=sec.marks_per_question,
                                        negative_marks=sec.negative_marks, question_ids=[q.id for q in qs]))
            questions += qs
            sittings.append(sitting)
            sources.append(src)
        dates = [f"{sec.subject}: {d}" for sec, d in zip(bp.sections, sittings) if d]
        note = "Held on " + ", ".join(dates) + "." if dates else ""
        if len(set(d for d in sittings if d)) > 1:
            note += " The papers come from different sittings of this exam."
        minutes = bp.duration_minutes
    else:
        secs = [bp.section(spec.subject)] if spec.kind == "subject" else bp.sections
        total = 0
        for sec in secs:
            if sec is None:
                raise RuntimeError(f"subject '{spec.subject}' is not in the exam pattern any more")
            n = spec.questions if spec.kind == "subject" else sec.questions
            total += n
            qs = writer.build_section(exam, bp, sec, bpmod.topic_plan(sec, n, spec.number), bpmod.difficulty_split(bp, n),
                                      lang, used_stems(series.id, sec.subject), series.diagrams, series.focus, progress)
            sections.append(TestSection(subject=sec.subject, marks_per_question=sec.marks_per_question,
                                        negative_marks=sec.negative_marks, question_ids=[q.id for q in qs]))
            questions += qs
        if len(questions) < total:
            note = f"{len(questions)} of {total} planned questions passed the answer check."
        if not questions:
            raise RuntimeError("no question passed the answer check")
        minutes = bp.duration_minutes if spec.kind == "full" else bpmod.scaled_minutes(bp, total)
        if spec.kind == "full" and len(questions) < total:
            minutes = bpmod.scaled_minutes(bp, len(questions))
    test = Test(id=spec.id, series_id=series.id, kind=spec.kind, order=spec.order, title=spec.title, subject=spec.subject,
                year=spec.year, language=lang, duration_minutes=minutes, sections=sections, questions=questions,
                access=spec.access, created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), note=note)
    d = save_test(test)
    if progress:
        progress(f"{spec.title}: rendering PDFs")
    try:
        from . import render

        render.render_pdfs(test, exam, bp, d)
    except Exception:
        log.exception("test PDFs failed (the online test still works)")
    return test


def skipped(series_id: str) -> dict[str, str]:
    """Tests the agent could not build (e.g. a year's paper isn't on the web): test id -> reason."""
    base = series_dir(series_id)
    out = {}
    for p in sorted(base.glob("*/skipped.json")) if base.exists() else []:
        try:
            out[p.parent.name] = json.loads(p.read_text(encoding="utf-8")).get("reason", "")
        except (ValueError, OSError):
            pass
    return out


def mark_skipped(series_id: str, test_id: str, reason: str) -> None:
    d = test_dir(series_id, test_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "skipped.json").write_text(json.dumps({"reason": reason, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
                                               ensure_ascii=False), encoding="utf-8")


def short_tests(series: control.TestSeries, bp: Blueprint) -> list[tuple[TestSpec, Test]]:
    """Built subject / full tests with fewer questions than planned (some failed the answer check)."""
    have = built(series.id)
    out = []
    for spec in plan(series, bp):
        t = have.get(spec.id)
        if t is not None and spec.kind != "pyq" and len(t.questions) < spec.questions:
            out.append((spec, t))
    return out


def top_up(cfg: control.ControlConfig, series: control.TestSeries, spec: TestSpec, test: Test, bp: Blueprint, progress=None) -> Test:
    """Write only the missing questions of a test (per section and topic) and render it again."""
    exam = exam_for(cfg, series)
    secs = [bp.section(spec.subject)] if spec.kind == "subject" else bp.sections
    by_section = {s.subject: s for s in test.sections}
    qs = test.by_id()
    for sec in secs:
        if sec is None:
            continue
        n = spec.questions if spec.kind == "subject" else sec.questions
        ts = by_section.get(sec.subject)
        current = [qs[i] for i in (ts.question_ids if ts else []) if i in qs]
        have: dict[str, int] = {}
        for q in current:
            have[q.topic] = have.get(q.topic, 0) + 1
        missing = [(t, c - have.get(t, 0)) for t, c in bpmod.topic_plan(sec, n, spec.number) if c - have.get(t, 0) > 0]
        short = n - len(current)
        if short <= 0:
            continue
        if sum(c for _, c in missing) < short:  # topics renamed since: fill with the section's mix
            missing = bpmod.topic_plan(sec, short, spec.number)
        if progress:
            progress(f"{spec.title} · {sec.subject}: topping up {short} question(s)")
        new = writer.build_section(exam, bp, sec, missing, bpmod.difficulty_split(bp, short), exam.language,
                                   used_stems(series.id, sec.subject), series.diagrams, series.focus, progress)[:short]
        if ts is None:
            ts = TestSection(subject=sec.subject, marks_per_question=sec.marks_per_question, negative_marks=sec.negative_marks, question_ids=[])
            test.sections.append(ts)
        ts.question_ids += [q.id for q in new]
        test.questions += new
    planned = spec.questions
    test.note = "" if len(test.questions) >= planned else f"{len(test.questions)} of {planned} planned questions passed the answer check."
    test.duration_minutes = bp.duration_minutes if spec.kind == "full" and not test.note else bpmod.scaled_minutes(bp, len(test.questions))
    d = save_test(test)
    try:
        from . import render

        render.render_pdfs(test, exam, bp, d)
    except Exception:
        log.exception("test PDFs failed")
    return test


def next_spec(series: control.TestSeries, bp: Blueprint) -> TestSpec | None:
    have, skip = built(series.id), skipped(series.id)
    return next((s for s in plan(series, bp) if s.id not in have and s.id not in skip), None)


def run(cfg: control.ControlConfig, series: control.TestSeries, what: str = "next", dry_run: bool = False,
        progress=None) -> str:
    """what: 'next' (build + release the next test), 'all' (every missing test), 'blueprint' (re-research the pattern)."""
    control.apply(cfg)
    if what == "blueprint":
        bp = ensure_blueprint(cfg, series, refresh=True)
        return f"{series.name}: pattern researched - {describe_blueprint(bp)}"
    bp = ensure_blueprint(cfg, series)
    lines = []
    for spec, test in short_tests(series, bp):  # finish incomplete tests first
        before = len(test.questions)
        try:
            test = top_up(cfg, series, spec, test, bp, progress)
            lines.append(f"➕ {test.title}: {before} -> {len(test.questions)} questions")
        except Exception as e:  # noqa: BLE001
            log.exception("top-up of %s failed", spec.id)
            lines.append(f"❌ top-up {spec.title}: {str(e)[:200]}")
    while True:
        spec = next_spec(series, bp)
        if spec is None:
            lines.append(f"{series.name}: every planned test is built ({len(plan(series, bp))}).")
            break
        if progress:
            progress(f"building {spec.title}")
        try:
            test = build_test(cfg, series, spec, bp, progress)
        except NoPaperFound as e:  # move on to the rest of the funnel; "delete" in the panel retries it
            mark_skipped(series.id, spec.id, str(e))
            lines.append(f"⏭ {spec.title}: {e}")
            continue
        except Exception as e:  # noqa: BLE001
            from ..providers import friendly_error

            log.exception("test %s failed", spec.id)
            lines.append(f"❌ {spec.title}: {friendly_error(str(e))[:300]}")
            break
        line = f"✅ {test.title}: {len(test.questions)} questions, {test.duration_minutes} min" + (f" ({test.note})" if test.note else "")
        if not dry_run:
            res = publish(cfg, series, test)
            line += f" · {res.get('summary', '')}"
        lines.append(line)
        if what != "all":
            break
    return "\n".join(lines)


def describe_blueprint(bp: Blueprint) -> str:
    secs = ", ".join(f"{s.subject} {s.questions}" for s in bp.sections)
    return f"{bp.total_questions} questions / {bp.total_marks:g} marks / {bp.duration_minutes} min ({secs})"


# ---------------------------------------------------------------- links & release

def test_url(series_id: str, test_id: str) -> str | None:
    base = settings.public_base_url
    return f"{base}/t/{series_id}/{test_id}" if base else None


def series_url(series_id: str) -> str | None:
    base = settings.public_base_url
    return f"{base}/t/{series_id}" if base else None


def publish(cfg: control.ControlConfig, series: control.TestSeries, test: Test) -> dict:
    """Post the test to the series' Telegram channels.

    free: announcement + link + question paper and solutions PDFs · signup: announcement, a sample question
    as a quiz poll and the sign-up link · private: nothing is posted."""
    from ..publish.telegram import Telegram, safe_html

    if test.access == "private":
        return {"summary": "private - not posted"}
    chans = [c for c in cfg.channels if c.id in series.channel_ids and c.enabled and c.configured() and c.platform == "telegram"]
    if not chans:
        return {"summary": "no Telegram channel connected"}
    if not settings.telegram_bot_token:
        return {"summary": "no Telegram bot token"}
    exam = exam_for(cfg, series)
    url = test_url(series.id, test.id)
    if test.access == "signup" and not url:
        return {"summary": "not posted: set the Public URL in Settings so students can open sign-up tests"}
    tg = Telegram()
    d = test_dir(series.id, test.id)
    head = {"pyq": "📜", "subject": "📘", "full": "🏁"}[test.kind]
    text = (f"{head} <b>{safe_html(test.title)}</b>\n{safe_html(exam.short_name)} · {safe_html(KINDS[test.kind])}\n\n"
            f"❓ {len(test.questions)} questions · 🧮 {test.total_marks:g} marks · ⏱ {test.duration_minutes} min")
    negs = sorted({s.negative_marks for s in test.sections if s.negative_marks})
    if negs:
        text += f"\n➖ Negative marking: {', '.join(format(n, 'g') for n in negs)}"
    if url:
        text += f"\n\n▶️ {'Attempt online (free sign-up)' if test.access == 'signup' else 'Attempt online with timer & rank'}: {url}"
    if (surl := series_url(series.id)):
        text += f"\n📚 All tests: {surl}"
    out = {"channels": {}}
    for ch in chans:
        ids = []
        try:
            ids.append(tg.send_message(ch.chat_id, text[:4096])["message_id"])
            if test.access == "free":
                for name, cap in (("paper.pdf", "📝 Question paper"), ("solutions.pdf", "✅ Answer key & solutions")):
                    if (d / name).exists():
                        ids.append(tg.send_document(ch.chat_id, d / name, f"{cap} · <b>{safe_html(test.title)}</b>")["message_id"])
            else:
                q = next((q for _, q in test.ordered() if not q.svg and len(q.options) <= 10), None)
                if q:
                    stem = (f"[{q.pyq_reference}] " if q.pyq_reference else "") + q.question
                    ids.append(tg.send_quiz(ch.chat_id, f"Sample: {stem}"[:300], q.options, q.correct_index, q.solution[:190])["message_id"])
            out["channels"][ch.name] = ids
        except Exception as e:  # noqa: BLE001
            log.exception("posting test to %s failed", ch.name)
            out["channels"][ch.name] = f"failed: {str(e)[:200]}"
    test.published = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **out}
    save_test(test)
    ok = [n for n, v in out["channels"].items() if isinstance(v, list)]
    out["summary"] = f"posted to {', '.join(ok)}" if ok else "posting failed"
    return out
