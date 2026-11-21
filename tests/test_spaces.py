"""Spaces: groups of exams + channels, each with its own agent that avoids repeating topics."""

from __future__ import annotations

import json
import random
import threading
import time

import pytest

from pathshala_bot import agent, control
from pathshala_bot.config import load_exams, settings


@pytest.fixture
def ctl(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTROL_FILE", str(tmp_path / "control.json"))
    saved = dict(vars(settings))
    object.__setattr__(settings, "output_dir", tmp_path)
    object.__setattr__(settings, "db_path", tmp_path / "h.sqlite3")
    yield control
    for k, v in saved.items():
        object.__setattr__(settings, k, v)


def _space(**kw):
    base = dict(id="police", name="Rajasthan Police", exam_ids=["rajasthan-si"])
    return control.Space(**{**base, **kw})


def test_old_settings_migrate_to_spaces(ctl, tmp_path):
    old = {
        "channels": [{"id": "a", "name": "A", "platform": "telegram", "chat_id": "@a"},
                     {"id": "b", "name": "B", "platform": "telegram", "chat_id": "@b"}],
        "exams": [{"exam_id": "rajasthan-si", "enabled": True, "weight": 3, "channel_ids": ["a"]},
                  {"exam_id": "jee-main", "enabled": True, "weight": 1, "language": "hinglish", "channel_ids": []},
                  {"exam_id": "neet-ug", "enabled": False, "weight": 1}],
        "schedules": [
            {"id": "s1", "name": "Raj posts", "kind": "content", "mode": "daily", "times": ["09:00"], "exam_ids": ["rajasthan-si"],
             "outputs": {"short": False, "pdf": True, "questions": True, "card": True}, "content_types": ["pyq_quiz"]},
            {"id": "s2", "name": "Everything", "kind": "content", "mode": "interval", "every_hours": 4, "enabled": False},
            {"id": "p", "name": "Paper", "kind": "akhbar", "mode": "daily", "times": ["07:00"]},
        ],
    }
    (tmp_path / "control.json").write_text(json.dumps(old))
    cfg = ctl.load()
    raj, everything = cfg.spaces
    assert (raj.id, raj.name, raj.exam_ids, raj.channel_ids) == ("s1", "Raj posts", ["rajasthan-si"], ["a"])
    assert raj.schedule.mode == "daily" and raj.schedule.times == ["09:00"] and not raj.outputs.short and raj.content_types == ["pyq_quiz"]
    assert everything.exam_ids == ["rajasthan-si", "jee-main"] and not everything.schedule.enabled
    assert everything.channel_ids == ["a"]  # the exams' channels
    assert [s.kind for s in cfg.schedules] == ["akhbar"]
    saved = json.loads((tmp_path / "control.json").read_text())
    assert "spaces" in saved and "exams" not in saved and "weight" not in json.dumps(saved)


def test_space_validation(ctl):
    cfg = ctl.load()
    view = ctl.public_view(cfg)
    view["spaces"].append({"id": "Bad Id", "name": "x", "exam_ids": ["jee-main"]})
    with pytest.raises(ValueError, match="lowercase"):
        ctl.merge_from_ui(cfg, view)
    view["spaces"][-1].update(id="science", exam_ids=[])
    with pytest.raises(ValueError, match="at least one exam"):
        ctl.merge_from_ui(cfg, view)
    view["spaces"][-1].update(exam_ids=["jee-main", "neet-ug"], channel_ids=["nope"])
    with pytest.raises(ValueError, match="unknown"):
        ctl.merge_from_ui(cfg, view)
    view["spaces"][-1].update(channel_ids=[], id=view["spaces"][0]["id"])
    with pytest.raises(ValueError, match="Two spaces"):
        ctl.merge_from_ui(cfg, view)
    view["spaces"][-1].update(id="science")
    merged = ctl.merge_from_ui(cfg, view)
    assert merged.space("science").exam_ids == ["jee-main", "neet-ug"]


def test_space_channels_only_connected_and_enabled(ctl):
    cfg = ctl.load()
    a = ctl.Channel(name="A", platform="telegram", chat_id="@a")
    b = ctl.Channel(name="B", platform="telegram", chat_id="@b")
    off = ctl.Channel(name="Off", platform="telegram", chat_id="@c", enabled=False)
    cfg.channels = [a, b, off]
    sp = _space(channel_ids=[b.id, off.id])
    assert [c.name for c in ctl.space_channels(cfg, sp)] == ["B"]
    assert ctl.space_channels(cfg, _space()) == []  # nothing connected = post nowhere
    assert ctl.find_space(control.ControlConfig(spaces=[sp]), "rajasthan police") is sp


def test_repeat_detection():
    recent = ["Integration of Rajasthan (7 stages)", "Wildlife sanctuaries of Rajasthan"]
    assert agent.is_repeat("integration of rajasthan - 7 stages", recent)
    assert agent.is_repeat("Wildlife Sanctuaries of Rajasthan", recent)
    assert not agent.is_repeat("Folk dances of Rajasthan: Ghoomar, Kalbeliya", recent)


def test_rule_planner_rotates_exams_and_subjects():
    exams = {k: v for k, v in load_exams().items() if k in ("rajasthan-si", "jee-main")}
    sp = _space(exam_ids=list(exams))
    rng = random.Random(1)
    recent = [{"exam_id": "rajasthan-si", "subject": "Rajasthan Geography", "topic": "Rivers of Rajasthan", "content_type": "cheatsheet"}]
    p = agent.plan_next(sp, exams, recent, rng=rng, use_ai=False)
    assert p.exam.id == "jee-main" and p.planner == "rules"  # the exam not served yet
    recent = [{"exam_id": "jee-main", "subject": "Physics", "topic": "x"}] + recent
    p = agent.plan_next(sp, exams, recent, rng=rng, use_ai=False)
    assert p.exam.id == "rajasthan-si" and p.subject != "Rajasthan Geography"  # served longest ago, a different subject
    assert p.topic in exams["rajasthan-si"].subjects[p.subject]


def test_ai_planner_retries_on_repeat_then_falls_back(monkeypatch):
    exams = {k: v for k, v in load_exams().items() if k in ("rajasthan-si", "jee-main")}
    sp = _space(exam_ids=list(exams), content_types=["pyq_quiz", "cheatsheet"])
    recent = [{"exam_id": "rajasthan-si", "subject": "Rajasthan History & Culture", "topic": "Integration of Rajasthan", "content_type": "cheatsheet"}]
    prompts = []
    answers = [
        agent.TopicPlan(exam_id="rajasthan-si", also_for=[], subject="Rajasthan History & Culture", topic="Integration of Rajasthan",
                        content_type="pyq_quiz", reason="x"),  # repeat -> rejected
        agent.TopicPlan(exam_id="rajasthan-si", also_for=["jee-main", "nope"], subject="rajasthan geography",
                        topic="Aravalli range: passes and peaks", content_type="formula_sheet", reason="new"),
    ]

    def fake_structured(system, user, model, max_tokens=0, effort=None):
        prompts.append(user)
        return answers[len(prompts) - 1]

    monkeypatch.setattr("pathshala_bot.llm.structured", fake_structured)
    p = agent.plan_next(sp, exams, recent, rng=random.Random(0))
    assert p.planner == "ai" and p.topic == "Aravalli range: passes and peaks"
    assert p.subject == "Rajasthan Geography"  # matched to the catalogue spelling
    assert p.also_for == ["jee-main"] and p.content_type in ("pyq_quiz", "cheatsheet")  # not an allowed format -> one that is
    assert "Integration of Rajasthan" in prompts[0] and "just rejected" in prompts[1]

    def broken(*a, **k):
        raise RuntimeError("no API key")

    monkeypatch.setattr("pathshala_bot.llm.structured", broken)
    assert agent.plan_next(sp, exams, recent, rng=random.Random(0)).planner == "rules"


def test_run_space_passes_plan_history_and_channels(ctl, monkeypatch):
    from pathshala_bot import pipeline
    from pathshala_bot.store import get_history

    cfg = ctl.load()
    ch = ctl.Channel(name="T", platform="telegram", chat_id="@t")
    other = ctl.Channel(name="Other", platform="telegram", chat_id="@o")
    cfg.channels = [ch, other]
    sp = _space(exam_ids=["rajasthan-si", "jee-main"], channel_ids=[ch.id], language="en",
                outputs=ctl.Outputs(short=False, pdf=False), memory=5)
    cfg.spaces = [sp]
    h = get_history()
    h.record("rajasthan-si", "Rajasthan Geography", "Rivers of Rajasthan", "cheatsheet", "d", "ok", {}, space_id="police")
    h.record("rajasthan-si", "x", "Old topic before spaces", "cheatsheet", "d", "ok", {})  # no space: still counts
    h.record("neet-ug", "x", "Other space topic", "cheatsheet", "d", "ok", {}, space_id="science")
    seen = {}
    monkeypatch.setattr(agent, "plan_with_ai", lambda *a, **k: agent.TopicPlan(
        exam_id="jee-main", also_for=["rajasthan-si"], subject="Physics", topic="Projectile motion range tricks", content_type="pyq_quiz", reason="r"))
    monkeypatch.setattr(pipeline, "run", lambda **kw: seen.update(kw) or "result")
    assert agent.run_space(cfg, sp) == "result"
    assert seen["seed_topic"] == "Projectile motion range tricks" and seen["subject"] == "Physics" and seen["exam_id"] == "jee-main"
    assert seen["space_id"] == "police" and seen["outputs"] == {"card", "questions"}
    assert [c.name for c in seen["channels"]] == ["T"]
    assert seen["avoid"] == ["Old topic before spaces", "Rivers of Rajasthan"]
    assert seen["exams"]["jee-main"].language == "en" and "Rajasthan SI" in seen["exams"]["jee-main"].audience
    agent.run_space(cfg, sp, dry_run=True)
    assert seen["channels"] == []


def test_history_records_space(ctl):
    from pathshala_bot.store import get_history

    h = get_history()
    h.record("jee-main", "Physics", "Topic A", "pyq_quiz", "d", "ok", {}, space_id="science")
    assert [r["topic"] for r in h.space_topics("science", ["jee-main"])] == ["Topic A"]
    assert h.space_topics("other", ["neet-ug"]) == []
    assert h.runs(1)[0]["space_id"] == "science"


def test_supabase_history_space_filter_and_old_schema():
    from pathshala_bot.store import SupabaseHistory

    class FakeDB:
        def __init__(self, has_column):
            self.has_column, self.inserted, self.selects = has_column, [], []

        def insert(self, table, row):
            if "space_id" in row and not self.has_column:
                raise RuntimeError("Supabase POST failed (400): Could not find the 'space_id' column of 'runs'")
            self.inserted.append(row)

        def select(self, table, params):
            self.selects.append(params)
            if not self.has_column and ("or" in params or "space_id" in params.get("select", "")):
                raise RuntimeError("column runs.space_id does not exist")
            return [{"topic": "T", "exam_id": "jee-main"}]

    new = FakeDB(True)
    SupabaseHistory(new).record("jee-main", "s", "T", "pyq_quiz", "d", "ok", {}, space_id="sci")
    assert new.inserted[0]["space_id"] == "sci"
    SupabaseHistory(new).space_topics("sci", ["jee-main", "neet-ug"])
    assert new.selects[-1]["or"] == '(space_id.eq.sci,and(space_id.is.null,exam_id.in.("jee-main","neet-ug")))'

    old = FakeDB(False)  # schema.sql not re-run yet: keeps working
    SupabaseHistory(old).record("jee-main", "s", "T", "pyq_quiz", "d", "ok", {}, space_id="sci")
    assert "space_id" not in old.inserted[0]
    assert SupabaseHistory(old).space_topics("sci", ["jee-main"])[0]["topic"] == "T"
    assert old.selects[-1]["exam_id"] == 'in.("jee-main")'


def test_spaces_run_independently(ctl, monkeypatch):
    from pathshala_bot import scheduler

    cfg = ctl.load()
    cfg.general.max_parallel_runs = 2
    cfg.spaces = [_space(id="one", name="One"), _space(id="two", name="Two")]
    ctl.save(cfg)
    gate = threading.Event()
    active, peak = [], []

    class R:
        def summary(self):
            return "ok"

    def fake_run_space(cfg, space, dry_run=False, **kw):
        active.append(space.id)
        peak.append(len(active))
        gate.wait(5)
        active.remove(space.id)
        return R()

    monkeypatch.setattr(agent, "run_space", fake_run_space)
    results = {}
    threads = [threading.Thread(target=lambda s=s: results.setdefault(s, scheduler.execute_space(s))) for s in ("one", "two")]
    for t in threads:
        t.start()
    for _ in range(100):
        if len(active) == 2:
            break
        time.sleep(0.02)
    assert sorted(active) == ["one", "two"]  # both spaces running at the same time
    assert "skipped" in scheduler.execute_space("one")  # the same space never overlaps itself
    assert {r["name"] for r in scheduler.Scheduler.state()["running"]} == {"Space · One", "Space · Two"}
    gate.set()
    for t in threads:
        t.join(5)
    assert results == {"one": "ok", "two": "ok"} and max(peak) == 2
    assert scheduler.Scheduler.state()["running"] == []


def test_scheduler_creates_a_job_per_space(ctl):
    from pathshala_bot.scheduler import Scheduler

    cfg = ctl.load()
    cfg.spaces = [_space(id="auto", name="Auto"), _space(id="manual", name="Manual", schedule=control.SpaceSchedule(enabled=False)),
                  _space(id="off", name="Off", enabled=False)]
    ctl.save(cfg)
    s = Scheduler()
    s.reload()
    assert [j.id for j in s.aps.get_jobs()] == ["space:auto"]


def test_web_run_space_endpoint(ctl, monkeypatch):
    from fastapi.testclient import TestClient

    from pathshala_bot.scheduler import Scheduler
    from pathshala_bot.web.app import create_app

    monkeypatch.setenv("ADMIN_PASSWORD", "pw")
    started = []
    monkeypatch.setattr(Scheduler, "run_space_now", lambda self, sid, dry_run=False: started.append((sid, dry_run)))
    with TestClient(create_app(with_bot=False)) as client:
        sid = client.get("/api/config", auth=("admin", "pw")).json()["config"]["spaces"][0]["id"]
        assert client.post(f"/api/spaces/{sid}/run?dry=true", auth=("admin", "pw")).json() == {"started": True, "dry_run": True}
        assert client.post("/api/spaces/nope/run", auth=("admin", "pw")).status_code == 404
    assert started == [(sid, True)]
