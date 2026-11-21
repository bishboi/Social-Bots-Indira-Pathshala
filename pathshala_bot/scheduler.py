"""Turns spaces and newspaper schedules from the admin UI into APScheduler jobs.

Every space runs independently: its own job, its own agent, its own history. A space
never overlaps itself (a firing while it is still running is skipped), and at most
`max_parallel_runs` runs (spaces + newspapers) render at the same time, since rendering
video is CPU-heavy.
"""

from __future__ import annotations

import logging
import threading
import traceback
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from . import control

log = logging.getLogger(__name__)

SPACE_JOB = "space:"  # job id prefix; newspaper jobs use the schedule id

_state_lock = threading.Lock()
_state: dict = {"running": {}, "last": []}  # shown on the dashboard
_busy: dict[str, threading.Lock] = {}  # one lock per space / schedule
_slots = {"n": 0, "sem": threading.BoundedSemaphore(2)}


def _slot(cfg: control.ControlConfig) -> threading.BoundedSemaphore:
    n = max(1, cfg.general.max_parallel_runs)
    with _state_lock:
        if _slots["n"] != n and not _state["running"]:
            _slots["n"], _slots["sem"] = n, threading.BoundedSemaphore(n)
        return _slots["sem"]


def _run_guarded(key: str, label: str, cfg: control.ControlConfig, fn) -> str:
    with _state_lock:
        lock = _busy.setdefault(key, threading.Lock())
    if not lock.acquire(blocking=False):
        return f"{label}: still running from the last time - skipped"
    try:
        with _state_lock:
            _state["running"][key] = {"name": label, "started": datetime.now().isoformat(timespec="seconds"), "state": "waiting"}
        with _slot(cfg):
            with _state_lock:
                _state["running"][key]["state"] = "running"
            try:
                summary = fn()
            except Exception as e:  # noqa: BLE001 - surfaced on the dashboard
                log.exception("%s failed", label)
                summary = f"FAILED: {e}\n{traceback.format_exc()[-800:]}"
    finally:
        with _state_lock:
            _state["running"].pop(key, None)
        lock.release()
    with _state_lock:
        _state["last"] = ([{"schedule": label, "finished": datetime.now().isoformat(timespec="seconds"), "summary": summary}] + _state["last"])[:30]
    return summary


def execute_space(space_id: str, dry_run: bool = False) -> str:
    """One run of a space's agent (blocking). Returns a short summary."""
    from . import agent

    cfg = control.load()
    control.apply(cfg)
    space = cfg.space(space_id)
    if space is None:
        return f"space {space_id} no longer exists"
    label = f"Space · {space.name}" + (" (dry run)" if dry_run else "")
    return _run_guarded(SPACE_JOB + space.id, label, cfg, lambda: agent.run_space(cfg, space, dry_run=dry_run).summary())


def execute(schedule_id: str, dry_run: bool = False) -> str:
    """One firing of a newspaper schedule (blocking). Returns a short summary."""
    cfg = control.load()
    control.apply(cfg)
    sched = cfg.schedule(schedule_id)
    if sched is None:
        return f"schedule {schedule_id} no longer exists"
    label = sched.name + (" (dry run)" if dry_run else "")
    return _run_guarded(sched.id, label, cfg, lambda: _execute_akhbar(cfg, sched, dry_run))


def _execute_akhbar(cfg: control.ControlConfig, sched: control.Schedule, dry_run: bool) -> str:
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


def trigger_for(sched: control.Timing, tz: str):
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
    error: str | None = None

    def __init__(self):
        self.aps = AsyncIOScheduler(job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 1800})

    def start(self):
        """Start even if the settings can't be loaded yet (the error is kept for the dashboard)."""
        self.aps.start()
        self.try_reload()

    def try_reload(self) -> bool:
        try:
            self.reload()
            self.error = None
            return True
        except Exception as e:  # database unreachable, tables missing, invalid settings...
            log.error("scheduler could not load settings: %s", e)
            self.error = str(e)[:600]
            return False

    def shutdown(self):
        if self.aps.running:
            self.aps.shutdown(wait=False)

    def reload(self):
        """(Re)create jobs from the saved config. Called after every save in the UI."""
        cfg = control.load()
        control.apply(cfg)
        for job in self.aps.get_jobs():
            job.remove()
        tz = cfg.general.timezone
        for sp in cfg.spaces:
            if sp.enabled and sp.schedule.enabled:
                self.aps.add_job(self._fire_space, trigger_for(sp.schedule, tz), args=[sp.id], id=SPACE_JOB + sp.id,
                                 name=f"Space · {sp.name}", replace_existing=True)
        for s in cfg.schedules:
            if s.enabled:
                self.aps.add_job(self._fire, trigger_for(s, tz), args=[s.id], id=s.id, name=s.name, replace_existing=True)
        log.info("scheduler loaded %d job(s)", len(self.aps.get_jobs()))

    @staticmethod
    def _today_allowed(timing: control.Timing, tz: str) -> bool:
        if timing.mode != "interval" or len(timing.days) == 7:
            return True  # daily schedules filter days in the cron trigger
        return datetime.now(ZoneInfo(tz)).strftime("%a").lower() in timing.days

    async def _fire_space(self, space_id: str):
        import asyncio

        cfg = control.load()
        sp = cfg.space(space_id)
        if sp and self._today_allowed(sp.schedule, cfg.general.timezone):
            await asyncio.to_thread(execute_space, space_id)

    async def _fire(self, schedule_id: str):
        import asyncio

        cfg = control.load()
        s = cfg.schedule(schedule_id)
        if s and self._today_allowed(s, cfg.general.timezone):
            await asyncio.to_thread(execute, schedule_id)

    def run_now(self, schedule_id: str, dry_run: bool = False):
        import asyncio

        return asyncio.get_running_loop().create_task(asyncio.to_thread(execute, schedule_id, dry_run))

    def run_space_now(self, space_id: str, dry_run: bool = False):
        import asyncio

        return asyncio.get_running_loop().create_task(asyncio.to_thread(execute_space, space_id, dry_run))

    def jobs(self) -> list[dict]:
        out = []
        for j in self.aps.get_jobs():
            nxt = getattr(j, "next_run_time", None)
            out.append({"id": j.id, "name": j.name, "next_run": nxt.isoformat(timespec="minutes") if nxt else None})
        return sorted(out, key=lambda x: x["next_run"] or "")

    @staticmethod
    def state() -> dict:
        with _state_lock:
            return {"running": list(_state["running"].values()), "last": list(_state["last"])}
