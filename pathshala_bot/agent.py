"""The agent of a space: decides what to post next, then runs the pipeline for it.

Each space (a group of exams + channels + content settings) has its own agent. On every
run it:
1. reads the topics this space already posted (newest first),
2. asks the AI to choose an exam, subject, specific topic and format that is clearly
   different from the recent ones and useful for the space's exams (a topic shared by
   several exams of the space is preferred when it fits),
3. checks the choice (allowed exam / format, not a repeat) and falls back to a
   rule-based pick if the AI is unavailable or repeats itself,
4. researches, generates, renders and posts to the space's channels.
"""

from __future__ import annotations

import difflib
import logging
import random
import re
from collections import Counter
from dataclasses import dataclass, field, replace

from pydantic import BaseModel, Field

from .config import Exam
from .models import CONTENT_TYPES

log = logging.getLogger(__name__)

SIMILAR = 0.82  # topics at least this similar (after normalising) count as a repeat


class TopicPlan(BaseModel):
    exam_id: str = Field(description="Id of the exam this post is mainly for (one of the space's exams).")
    also_for: list[str] = Field(description="Other exam ids of the space this topic is equally useful for (may be empty).")
    subject: str = Field(description="Subject name exactly as listed for that exam.")
    topic: str = Field(description="One specific, high-yield topic (narrower than the seed topics), different from every recent topic.")
    content_type: str = Field(description="Format key, one of the allowed formats.")
    reason: str = Field(description="One line: why this topic now, and how it differs from recent posts.")


@dataclass
class Plan:
    exam: Exam
    subject: str
    topic: str
    content_type: str | None
    also_for: list[str] = field(default_factory=list)
    reason: str = ""
    planner: str = "ai"  # "ai" or "rules"


def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", " ", (text or "").lower(), flags=re.UNICODE).strip()


def is_repeat(topic: str, recent: list[str], threshold: float = SIMILAR) -> bool:
    t = _norm(topic)
    if not t:
        return True
    for r in recent:
        n = _norm(r)
        if not n:
            continue
        if t == n or (len(t) > 12 and (t in n or n in t)) or difflib.SequenceMatcher(None, t, n).ratio() >= threshold:
            return True
    return False


def _match_subject(exam: Exam, subject: str) -> str | None:
    if subject in exam.subjects:
        return subject
    by_norm = {_norm(s): s for s in exam.subjects}
    if _norm(subject) in by_norm:
        return by_norm[_norm(subject)]
    close = difflib.get_close_matches(_norm(subject), list(by_norm), n=1, cutoff=0.6)
    return by_norm[close[0]] if close else None


def plan_with_rules(exams: dict[str, Exam], recent: list[dict], allowed_types: list[str], rng: random.Random) -> Plan:
    """No-AI fallback: least-recently-served exam, then the least-recently-used subject and an unused seed topic."""
    order = [r["exam_id"] for r in recent]  # newest first
    # an exam never served in this space, else the one served longest ago
    never = [e for e in exams.values() if e.id not in order]
    exam = rng.choice(never) if never else max(exams.values(), key=lambda e: order.index(e.id))
    subj_recent = [r.get("subject") for r in recent if r["exam_id"] == exam.id]
    unused = [s for s in exam.subjects if s not in subj_recent]
    subject = rng.choice(unused) if unused else max(exam.subjects, key=lambda s: subj_recent.index(s))
    topics = [r["topic"] for r in recent]
    seeds = [t for t in exam.subjects[subject] if not is_repeat(t, topics, 0.7)] or list(exam.subjects[subject])
    return Plan(exam=exam, subject=subject, topic=rng.choice(seeds), content_type=rng.choice(allowed_types) if allowed_types else None,
                reason="rule-based pick (least recently covered exam/subject)", planner="rules")


def _catalogue(exams: dict[str, Exam], seeds_per_subject: int = 12) -> str:
    out = []
    for e in exams.values():
        out.append(f"EXAM {e.id}: {e.name} ({e.short_name}) - audience: {e.audience or 'aspirants'} - language: {e.language}")
        for subj, seeds in e.subjects.items():
            out.append(f"  SUBJECT \"{subj}\": " + "; ".join(seeds[:seeds_per_subject]))
    return "\n".join(out)


def plan_with_ai(space_name: str, focus: str, exams: dict[str, Exam], recent: list[dict], allowed_types: list[str],
                 avoid_extra: list[str] | None = None) -> TopicPlan:
    from datetime import date

    from .llm import structured

    hist = "\n".join(f"- {r.get('created_at', '')[:10]} [{r['exam_id']} / {r.get('subject') or '?'} / {r.get('content_type') or '?'}] {r['topic']}"
                     for r in recent) or "- (nothing posted yet)"
    usage = Counter(r["exam_id"] for r in recent)
    balance = ", ".join(f"{e}: {usage.get(e, 0)}" for e in exams)
    formats = "\n".join(f"- {k}: {CONTENT_TYPES[k]}" for k in allowed_types)
    rejected = ("\nThese were just rejected as repeats, choose something else:\n" + "\n".join(f"- {t}" for t in avoid_extra)) if avoid_extra else ""
    prompt = f"""You plan the next post for the space "{space_name}" of an Indian exam-prep channel. Today is {date.today().isoformat()}.

The space serves these exams (subjects and seed topics; you may go beyond the seeds within the syllabus):
{_catalogue(exams)}

{('Space focus set by the admin: ' + focus) if focus.strip() else ''}

Posts already made in this space, newest first:
{hist}

Posts per exam so far: {balance}

Allowed formats:
{formats}
{rejected}
Choose the next post:
- The topic must be clearly different from the recent ones (not the same topic reworded, not a sub-part of a topic from the last few posts). Rotate subjects as well.
- Keep the exams of the space balanced over time: prefer an exam that has had fewer posts recently, unless a topic serves several exams at once.
- When the space has several exams, a topic that appears in more than one syllabus is good value: list those exams in also_for.
- Prefer high-yield topics that are frequently asked in these exams.
- Pick the format that suits the topic best."""
    return structured("You are the content planner of an exam-prep channel. Reply with the plan only.", prompt, TopicPlan,
                      max_tokens=4000, effort="low")


def plan_next(space, exams: dict[str, Exam], recent: list[dict], rng: random.Random | None = None, use_ai: bool = True) -> Plan:
    rng = rng or random.Random()
    if not exams:
        raise RuntimeError(f"Space '{space.name}' has no exams")
    allowed = [t for t in (space.content_types or list(CONTENT_TYPES)) if t in CONTENT_TYPES]
    topics = [r["topic"] for r in recent]
    rejected: list[str] = []
    if use_ai:
        for _ in range(2):
            try:
                tp = plan_with_ai(space.name, space.focus, exams, recent, allowed, rejected)
            except Exception as e:  # no key, provider error, refusal...
                log.warning("space %s: AI planner failed (%s), using rules", space.id, e)
                break
            exam = exams.get(tp.exam_id)
            subject = _match_subject(exam, tp.subject) if exam else None
            if exam is None or subject is None:
                log.warning("space %s: planner chose unknown exam/subject %s/%s", space.id, tp.exam_id, tp.subject)
                rejected.append(tp.topic)
                continue
            if is_repeat(tp.topic, topics):
                log.info("space %s: planner repeated a recent topic: %s", space.id, tp.topic)
                rejected.append(tp.topic)
                continue
            ctype = tp.content_type if tp.content_type in allowed else (rng.choice(space.content_types) if space.content_types else None)
            return Plan(exam=exam, subject=subject, topic=tp.topic.strip(), content_type=ctype,
                        also_for=[e for e in tp.also_for if e in exams and e != exam.id], reason=tp.reason)
    return plan_with_rules(exams, recent, space.content_types, rng)


def with_also_for(exam: Exam, others: list[Exam]) -> Exam:
    """The same exam, with the audience widened to the other exams the topic serves."""
    if not others:
        return exam
    names = ", ".join(o.short_name for o in others)
    return replace(exam, audience=f"{exam.audience}. Also useful for aspirants of: {names}".strip(". "))


def run_space(cfg, space, dry_run: bool = False, channels: list | None = None, exam_id: str | None = None,
              content_type: str | None = None, video_style: str | None = None, use_ai_planner: bool = True,
              rng: random.Random | None = None):
    """One run of a space's agent (blocking). Returns pipeline.RunResult.

    channels: post here instead of the space's channels (bot /preview); exam_id/content_type/video_style:
    narrow the agent's choice (bot /generate <exam> <type> <style>)."""
    from . import control, pipeline
    from .store import get_history

    exams = control.space_exams(cfg, space)
    if not exams:
        raise RuntimeError(f"Space '{space.name}': none of its exams exist any more - edit the space")
    recent = get_history().space_topics(space.id, list(exams), limit=space.memory) if space.memory else []
    pool = {exam_id: exams[exam_id]} if exam_id in exams else exams
    if content_type:
        space = space.model_copy(update={"content_types": [content_type]})
    plan = plan_next(space, pool, recent, rng=rng, use_ai=use_ai_planner)
    log.info("space %s plan (%s): %s / %s / %s / %s - %s", space.id, plan.planner, plan.exam.id, plan.subject, plan.topic,
             plan.content_type, plan.reason)
    rng = rng or random.Random()
    style = video_style or (rng.choice(space.video_styles) if space.video_styles else None)
    if channels is None:
        channels = [] if dry_run else control.space_channels(cfg, space)
    exam = with_also_for(plan.exam, [exams[e] for e in plan.also_for])
    return pipeline.run(
        exam_id=exam.id, exams={**exams, exam.id: exam}, subject=plan.subject, seed_topic=plan.topic,
        content_type=plan.content_type, video_style=style, outputs=space.outputs.as_set(), channels=channels,
        space_id=space.id, avoid=[r["topic"] for r in recent], plan_note=f"{plan.planner}: {plan.reason}",
    )
