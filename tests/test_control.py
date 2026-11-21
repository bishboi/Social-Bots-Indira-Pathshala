"""Admin config, scheduler, web API and the daily newspaper (offline)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from pathshala_bot import control

AKHBAR_FIXTURE = Path(__file__).parent / "fixtures" / "akhbar_sample_layout.json"


@pytest.fixture
def ctl(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTROL_FILE", str(tmp_path / "control.json"))
    from pathshala_bot.config import settings

    saved = dict(vars(settings))
    object.__setattr__(settings, "output_dir", tmp_path)
    object.__setattr__(settings, "db_path", tmp_path / "h.sqlite3")
    yield control
    for k, v in saved.items():  # control.apply() mutates the shared settings object
        object.__setattr__(settings, k, v)


def test_seed_and_roundtrip(ctl):
    cfg = ctl.load()
    assert {e.exam_id for e in cfg.exams} >= {"rajasthan-si", "jee-main"}
    assert any(s.kind == "akhbar" for s in cfg.schedules)
    cfg.general.brand_name = "Test Brand"
    ctl.save(cfg)
    assert ctl.load().general.brand_name == "Test Brand"


def test_secrets_are_masked_and_preserved(ctl):
    cfg = ctl.load()
    cfg.secrets.telegram_bot_token = "123456:SECRET-TOKEN-abcd"
    cfg.channels = [ctl.Channel(name="YT", platform="youtube", client_id="cid", client_secret="topsecret-xyz1", refresh_token="refresh-9999")]
    ctl.save(cfg)
    view = ctl.public_view(ctl.load())
    assert "SECRET" not in json.dumps(view)
    assert view["secrets"]["telegram_bot_token"].endswith("abcd")
    # the browser sends masks back: stored values must survive
    view["general"]["brand_name"] = "Edited"
    merged = ctl.merge_from_ui(ctl.load(), view)
    assert merged.secrets.telegram_bot_token == "123456:SECRET-TOKEN-abcd"
    assert merged.channels[0].client_secret == "topsecret-xyz1"
    # a newly typed value replaces it
    view["secrets"]["telegram_bot_token"] = "999:NEW"
    assert ctl.merge_from_ui(ctl.load(), view).secrets.telegram_bot_token == "999:NEW"


def test_validation_rejects_bad_schedules(ctl):
    view = ctl.public_view(ctl.load())
    view["schedules"][0]["mode"] = "daily"
    view["schedules"][0]["times"] = ["25:00"]
    with pytest.raises(ValueError, match="HH:MM"):
        ctl.merge_from_ui(ctl.load(), view)
    view = ctl.public_view(ctl.load())
    view["schedules"][0]["exam_ids"] = ["no-such-exam"]
    with pytest.raises(ValueError, match="unknown"):
        ctl.merge_from_ui(ctl.load(), view)


def test_language_override_and_channel_routing(ctl):
    cfg = ctl.load()
    a = ctl.Channel(name="A", platform="telegram", chat_id="@a")
    b = ctl.Channel(name="B", platform="telegram", chat_id="@b")
    off = ctl.Channel(name="Off", platform="telegram", chat_id="@c", enabled=False)
    cfg.channels = [a, b, off]
    for es in cfg.exams:
        if es.exam_id == "jee-main":
            es.language = "hinglish"
            es.channel_ids = [b.id]
        if es.exam_id == "neet-ug":
            es.enabled = False
    exams = ctl.effective_exams(cfg)
    assert exams["jee-main"].language == "hinglish" and "neet-ug" not in exams
    assert ctl.effective_exams(cfg, "en")["rajasthan-si"].language == "en"  # schedule-level override
    assert [c.name for c in ctl.channels_for(cfg, "jee-main")] == ["B"]
    assert [c.name for c in ctl.channels_for(cfg, "rajasthan-si")] == ["A", "B"]  # none chosen = all enabled
    sched = ctl.Schedule(name="s", channel_ids=[a.id])
    assert [c.name for c in ctl.channels_for(cfg, "jee-main", sched)] == ["A"]  # schedule wins


def test_custom_exam(ctl):
    cfg = ctl.load()
    view = ctl.public_view(cfg)
    view["custom_exams"] = [{"id": "raj-patwari", "name": "Rajasthan Patwari", "short_name": "Patwari", "language": "hi",
                             "audience": "x", "subjects": {"Rajasthan GK": ["Rivers"]}}]
    ctl.save(ctl.merge_from_ui(cfg, view))
    cfg = ctl.load()
    es = next(e for e in cfg.exams if e.exam_id == "raj-patwari")
    es.enabled = True
    assert ctl.effective_exams(cfg)["raj-patwari"].subjects == {"Rajasthan GK": ["Rivers"]}


def test_triggers():
    from pathshala_bot.scheduler import trigger_for

    s = control.Schedule(name="i", mode="interval", every_hours=4, start_at="07:00")
    t = trigger_for(s, "Asia/Kolkata")
    first = t.get_next_fire_time(None, t.start_date)
    assert first.hour in (7, 11, 15, 19, 23, 3) and first.minute == 0
    d = control.Schedule(name="d", mode="daily", times=["08:00", "18:30"], days=["mon", "wed"])
    td = trigger_for(d, "Asia/Kolkata")
    from datetime import datetime
    from zoneinfo import ZoneInfo

    nxt = td.get_next_fire_time(None, datetime(2026, 11, 23, 9, 0, tzinfo=ZoneInfo("Asia/Kolkata")))  # a Monday
    assert (nxt.weekday(), nxt.hour, nxt.minute) == (0, 18, 30)


def test_execute_content_schedule_uses_outputs_and_channels(ctl, monkeypatch):
    from pathshala_bot import pipeline, scheduler

    cfg = ctl.load()
    ch = ctl.Channel(name="T", platform="telegram", chat_id="@t")
    cfg.channels = [ch]
    s = ctl.Schedule(name="Quiz only", exam_ids=["jee-main"], content_types=["pyq_quiz"],
                     outputs=ctl.Outputs(card=False, pdf=False, short=False, questions=True))
    cfg.schedules = [s]
    ctl.save(cfg)
    seen = {}

    class R:
        def summary(self):
            return "ok"

    def fake_run(**kw):
        seen.update(kw)
        return R()

    monkeypatch.setattr(pipeline, "run", fake_run)
    assert scheduler.execute(s.id) == "ok"
    assert seen["exam_id"] == "jee-main" and seen["content_type"] == "pyq_quiz"
    assert seen["outputs"] == {"questions"} and [c.name for c in seen["channels"]] == ["T"]
    scheduler.execute(s.id, dry_run=True)
    assert seen["channels"] == []


def test_web_api_auth_and_config(ctl, monkeypatch):
    from fastapi.testclient import TestClient

    from pathshala_bot.web.app import create_app

    monkeypatch.setenv("ADMIN_PASSWORD", "pw")
    with TestClient(create_app(with_bot=False)) as client:
        assert client.get("/api/config").status_code == 401
        assert client.get("/api/config", auth=("admin", "nope")).status_code == 401
        r = client.get("/api/config", auth=("admin", "pw"))
        assert r.status_code == 200 and "exam_defs" in r.json()
        cfg = r.json()["config"]
        cfg["schedules"][0]["every_hours"] = 3
        cfg["schedules"][0]["mode"] = "interval"
        assert client.put("/api/config", json=cfg, auth=("admin", "pw")).status_code == 200
        st = client.get("/api/status", auth=("admin", "pw")).json()
        assert any(j["name"] == cfg["schedules"][0]["name"] for j in st["jobs"])
        cfg["general"]["timezone"] = "Mars/Olympus"
        assert client.put("/api/config", json=cfg, auth=("admin", "pw")).status_code == 400
        assert client.get("/files/../control.json/x", auth=("admin", "pw")).status_code == 404


# ---------------- daily newspaper ----------------

def _edition():
    from pathshala_bot.akhbar import AkhbarEdition

    return AkhbarEdition.model_validate_json(AKHBAR_FIXTURE.read_text(encoding="utf-8"))


def test_akhbar_checks_drop_stale_and_unsourced():
    from pathshala_bot.akhbar import check_edition

    ed = _edition()
    day = date(2026, 11, 21)
    ed.articles[1].date = (day - timedelta(days=9)).isoformat()
    ed.articles[2].sources = []
    ed.articles[3].date = "recently"
    notes = check_edition(ed, day)
    assert len(notes) == 3
    assert all(a.sources and a.date for a in ed.articles)
    assert [a.importance for a in ed.articles] == sorted([a.importance for a in ed.articles], reverse=True)


def test_akhbar_render_and_publish(ctl, monkeypatch, tmp_path):
    pytest.importorskip("weasyprint")
    from pathshala_bot import akhbar
    from pathshala_bot.config import load_exams
    from pathshala_bot.publish import telegram

    calls = []

    def fake_call(self, method, data=None, files=None, json=None):
        calls.append(method)
        return {"message_id": len(calls)}

    monkeypatch.setattr(telegram.Telegram, "call", fake_call)
    monkeypatch.setattr("time.sleep", lambda s: None)
    object.__setattr__(akhbar.settings, "telegram_bot_token", "1:x")
    ch = ctl.Channel(name="T", platform="telegram", chat_id="@t")
    res = akhbar.run(akhbar.Desk("Rajasthan", "hi"), channels=[ch], from_json=AKHBAR_FIXTURE)
    assert res.ok and res.pdf.read_bytes()[:4] == b"%PDF"
    assert calls[0] == "sendDocument" and calls.count("sendPoll") == len(res.edition.quiz)
    manifest = json.loads((res.run_dir / "manifest.json").read_text())
    assert manifest["kind"] == "akhbar" and len(manifest["articles"]) == len(res.edition.articles)
    assert manifest["edition"] == "rajasthan-hi" and manifest["region"] == "Rajasthan"
    html = next(res.run_dir.glob("*.html")).read_text(encoding="utf-8")
    assert "राजस्थान संस्करण" in html


def test_editions_routing_and_validation(ctl):
    from pathshala_bot import akhbar

    cfg = ctl.load()
    assert {(e.region, e.language) for e in cfg.editions} >= {("Rajasthan", "hi"), ("central", "hi")}
    raj_ch = ctl.Channel(name="Raj", platform="telegram", chat_id="@raj")
    nat_ch = ctl.Channel(name="Nat", platform="telegram", chat_id="@nat")
    yt = ctl.Channel(name="YT", platform="youtube", client_id="a", client_secret="b", refresh_token="c")
    cfg.channels = [raj_ch, nat_ch, yt]
    raj = next(e for e in cfg.editions if e.region == "Rajasthan")
    raj.channel_ids = [raj_ch.id]
    central = next(e for e in cfg.editions if e.region == "central" and e.language == "hi")
    assert [c.name for c in ctl.channels_for_edition(cfg, raj)] == ["Raj"]
    assert [c.name for c in ctl.channels_for_edition(cfg, central)] == ["Raj", "Nat"]  # telegram only, all
    sched = ctl.Schedule(name="paper", kind="akhbar", edition_ids=[central.id])
    assert ctl.editions_for(cfg, sched) == [central]
    # same paper key for a region+language, whatever the edition id
    assert akhbar.Desk("Rajasthan", "hi").key == raj.desk().key == "rajasthan-hi"
    assert akhbar.Desk("central", "en").label() == "National edition"
    view = ctl.public_view(cfg)
    view["editions"].append({"id": "x", "region": "Rajasthan", "language": "hi", "enabled": True, "channel_ids": []})
    with pytest.raises(ValueError, match="same language"):
        ctl.merge_from_ui(cfg, view)
    view["editions"][-1].update(region="Uttar Pradesh", id="stale-id")
    sched_view = view["schedules"][1]
    sched_view["edition_ids"] = ["stale-id"]
    merged = ctl.merge_from_ui(cfg, view)
    assert merged.editions[-1].id == "uttar-pradesh-hi" and merged.schedules[1].edition_ids == ["uttar-pradesh-hi"]
    view["editions"][-1]["region"] = "Atlantis"
    with pytest.raises(ValueError, match="unknown region"):
        ctl.merge_from_ui(cfg, view)


def test_akhbar_schedule_runs_each_edition(ctl, monkeypatch):
    from pathshala_bot import akhbar, scheduler

    cfg = ctl.load()
    cfg.channels = [ctl.Channel(name="T", platform="telegram", chat_id="@t")]
    for e in cfg.editions:
        e.enabled = e.language == "hi"
    s = ctl.Schedule(name="paper", kind="akhbar", mode="daily", times=["07:00"])
    cfg.schedules = [s]
    ctl.save(cfg)
    desks = []

    class R:
        def summary(self):
            return "ok"

    monkeypatch.setattr(akhbar, "run", lambda desk, channels=None, questions=True: desks.append((desk.region, desk.language, len(channels))) or R())
    scheduler.execute(s.id)
    assert sorted(desks) == [("Rajasthan", "hi", 1), ("central", "hi", 1)]


def test_newspaper_layout_sections():
    from pathshala_bot.akhbar import FRONT_PAGE_STORIES, layout

    ed = _edition()
    front, sections = layout(ed.articles)
    assert [i for i, _ in front] == list(range(FRONT_PAGE_STORIES))
    placed = [i for _, items in sections for i, _ in items]
    assert sorted(placed) == list(range(FRONT_PAGE_STORIES, len(ed.articles)))  # every story appears exactly once
    keys = [k for k, _ in sections]
    assert keys.index("world") < keys.index("sports")  # section order follows a newspaper


@pytest.mark.parametrize("region,lang,title", [("Rajasthan", "hi", "पाठशाला समाचार"), ("central", "en", "Pathshala Times")])
def test_newspaper_renders_each_edition(ctl, tmp_path, region, lang, title):
    pytest.importorskip("weasyprint")
    from datetime import date

    from pathshala_bot import akhbar

    out = akhbar.render(_edition(), akhbar.Desk(region, lang), date(2026, 11, 21), 3, tmp_path / "p.pdf", title, photos={})
    assert out.read_bytes()[:4] == b"%PDF"
    html = out.with_suffix(".html").read_text(encoding="utf-8")
    assert akhbar.Desk(region, lang).label() in html and "target-counter" in html
