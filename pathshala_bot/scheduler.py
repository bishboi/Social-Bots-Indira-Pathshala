"""Turns the schedules configured in the admin UI into APScheduler jobs.

Runs are executed one at a time in a worker thread (rendering is CPU-heavy and
posting order matters), queued if a previous run is still going.
"""

from __future__ import annotations

import logging
import random
import threading
import traceback
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from . import control

log = logging.getLogger(__name__)

_worker_lock = threading.Lock()
_state = {"running": None, "last": []}  # shown on the dashboard


def _pick_exam(cfg: control.ControlConfig, sched: control.Schedule, rng: random.Random):
    pool = control.effective_exams(cfg, sched.language)
    if sched.exam_ids:
        pool = {k: v for k, v in pool.items() if k in sched.exam_ids}
    if not pool:
        raise RuntimeError(f"Schedule '{sched.name}': no enabled exam to target")
    exams = list(pool.values())
    return rng.choices(exams, weights=[max(0.01, e.weight) for e in exams])[0], pool


def execute(schedule_id: str, dry_run: bool = False) -> str:
    """Run one firing of a schedule (blocking). Returns a short summary."""
    cfg = control.load()
    control.apply(cfg)
    sched = cfg.schedule(schedule_id)
    if sched is None:
        return f"schedule {schedule_id} no longer exists"
    with _worker_lock:
        _state["running"] = {"schedule": sched.name, "started": datetime.now().isoformat(timespec="seconds")}
        try:
            summary = _execute(cfg, sched, dry_run)
        except Exception as e:  # noqa: BLE001 - surfaced on the dashboard
            log.exception("schedule %s failed", sched.name)
            summary = f"FAILED: {e}\n{traceback.format_exc()[-800:]}"
        finally:
            _state["running"] = None
        _state["last"] = ([{"schedule": sched.name, "finished": datetime.now().isoformat(timespec="seconds"), "summary": summary}] + _state["last"])[:20]
        return summary


def _execute(cfg: control.ControlConfig, sched: control.Schedule, dry_run: bool) -> str:
    rng = random.Random()
    if sched.kind == "akhbar":
        from . import akhbar

        editions = control.editions_for(cfg, sched)
        if not editions:
            raise RuntimeError("no enabled newspaper edition for this schedule")
        out = []
        for ed in editions:
            desk = akhbar.Desk(region=ed.region, language=sched.language or ed.language)
            chans = [] if dry_run else control.channels_for_edition(cfg, ed, sched)
            out.append(akhbar.run(desk, channels=chans, questions=sched.outputs.questions).summary())
        return "\n\n".join(out)

    from . import pipeline

    exam, pool = _pick_exam(cfg, sched, rng)
    ctype = rng.choice(sched.content_types) if sched.content_types else None
    style = rng.choice(sched.video_styles) if sched.video_styles else None
    chans = [] if dry_run else control.channels_for(cfg, exam.id, sched)
    res = pipeline.run(exam_id=exam.id, content_type=ctype, video_style=style, exams=pool,
                       outputs=sched.outputs.as_set(), channels=chans)
    return res.summary()


def trigger_for(sched: control.Schedule, tz: str):
    zone = ZoneInfo(tz)
    days = ",".join(sched.days) if len(sched.days) < 7 else "*"
    if sched.mode == "daily":
        from apscheduler.triggers.combining import OrTrigger

        crons = [CronTrigger(hour=int(t[:2]), minute=int(t[3:]), day_of_week=days, timezone=zone) for t in sched.times]
        return crons[0] if len(crons) == 1 else OrTrigger(crons)
    hh, mm = int(sched.start_at[:2]), int(sched.start_at[3:])
    now = datetime.now(zone)
    start = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if start > now:
        start -= timedelta(days=1)  # anchor in the past so the next slot lines up with start_at
    return IntervalTrigger(minutes=int(sched.every_hours * 60), start_date=start, timezone=zone)


class Scheduler:
    def __init__(self):
        self.aps = AsyncIOScheduler(job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 1800})

    def start(self):
        self.reload()
        self.aps.start()

    def shutdown(self):
        if self.aps.running:
            self.aps.shutdown(wait=False)

    def reload(self):
        """(Re)create jobs from the saved config. Called after every save in the UI."""
        cfg = control.load()
        control.apply(cfg)
        for job in self.aps.get_jobs():
            job.remove()
        for s in cfg.schedules:
            if not s.enabled:
                continue
            self.aps.add_job(self._fire, trigger_for(s, cfg.general.timezone), args=[s.id], id=s.id, name=s.name, replace_existing=True)
        log.info("scheduler loaded %d job(s)", len(self.aps.get_jobs()))

    async def _fire(self, schedule_id: str):
        import asyncio

        cfg = control.load()
        s = cfg.schedule(schedule_id)
        if s and s.mode == "interval" and len(s.days) < 7:
            today = datetime.now(ZoneInfo(cfg.general.timezone)).strftime("%a").lower()
            if today not in s.days:
                return
        await asyncio.to_thread(execute, schedule_id)

    def run_now(self, schedule_id: str, dry_run: bool = False):
        import asyncio

        return asyncio.get_running_loop().create_task(asyncio.to_thread(execute, schedule_id, dry_run))

    def jobs(self) -> list[dict]:
        out = []
        for j in self.aps.get_jobs():
            nxt = getattr(j, "next_run_time", None)
            out.append({"id": j.id, "name": j.name, "next_run": nxt.isoformat(timespec="minutes") if nxt else None})
        return sorted(out, key=lambda x: x["next_run"] or "")

    @staticmethod
    def state() -> dict:
        return {"running": _state["running"], "last": _state["last"]}
