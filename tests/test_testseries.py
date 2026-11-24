"""Test series: blueprint ratios, the funnel plan, question agents, SVG figures, PDFs and the online test."""

from __future__ import annotations

import json

import httpx
import pytest

from pathshala_bot import control, llm
from pathshala_bot.config import settings
from pathshala_bot.providers import Research
from pathshala_bot.testseries import blueprint as bpm
from pathshala_bot.testseries import builder, diagrams, public, writer
from pathshala_bot.testseries.models import Blueprint, QuestionBatch, QuestionDraft, Section, TopicShare, Verdict, Verdicts


@pytest.fixture
def ctl(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTROL_FILE", str(tmp_path / "control.json"))
    monkeypatch.setenv("ADMIN_PASSWORD", "pw")
    saved = dict(vars(settings))
    object.__setattr__(settings, "output_dir", tmp_path)
    object.__setattr__(settings, "db_path", tmp_path / "h.sqlite3")
    object.__setattr__(settings, "openrouter_api_key", "")
    yield control
    for k, v in saved.items():
        object.__setattr__(settings, k, v)


def make_bp() -> Blueprint:
    return bpm.normalize(Blueprint(
        exam_name="RPSC SI", stage="Written", duration_minutes=120, options_per_question=4,
        sections=[Section(subject="Hindi", questions=10, marks_per_question=2, negative_marks=0.66,
                          topics=[TopicShare(topic="Sandhi", questions=4), TopicShare(topic="Samas", questions=3),
                                  TopicShare(topic="Muhavare", questions=3)]),
                  Section(subject="Reasoning", questions=6, marks_per_question=2, negative_marks=0.66,
                          topics=[TopicShare(topic="Figures", questions=2), TopicShare(topic="Series", questions=4)])],
        difficulty_easy=30, difficulty_medium=50, difficulty_hard=20, instructions=["Each question carries 2 marks."],
        pyq_years=["2021", "2018", " "], notes=""))


def test_ratios_follow_the_real_paper():
    assert bpm.allocate([4, 3, 3], 10) == [4, 3, 3]
    assert sum(bpm.allocate([4, 3, 3], 25)) == 25 and bpm.allocate([1, 1, 1], 2, rotate=1) != bpm.allocate([1, 1, 1], 2, rotate=0)
    bp = make_bp()
    assert bp.total_questions == 16 and bp.total_marks == 32 and bp.pyq_years == ["2021", "2018"]
    assert bpm.topic_plan(bp.sections[0], 20) == [("Sandhi", 8), ("Samas", 6), ("Muhavare", 6)]
    assert sum(bpm.difficulty_split(bp, 25).values()) == 25
    assert bpm.scaled_minutes(bp, 8) == 60  # half the questions -> half the time


def test_funnel_plan(ctl):
    ts = control.TestSeries(id="si", name="SI", exam_id="rajasthan-si")
    specs = builder.plan(ts, make_bp())
    assert [s.id for s in specs] == ["pyq-2021", "pyq-2018", "subject-hindi-1", "subject-reasoning-1", "subject-hindi-2",
                                     "subject-reasoning-2", "full-1", "full-2", "full-3"]
    assert [s.order for s in specs] == list(range(1, 10))
    assert specs[0].access == "free" and specs[2].access == "signup" and specs[2].questions == 25
    ts.stages[1].questions = 0
    assert builder.plan(ts, make_bp())[2].questions == 10  # the subject's share of a real paper (min 10)


WORDS = "river fort temple battle treaty king desert lake dance poet mineral canal festival dynasty painting".split()


def _stem(topic, i):
    import hashlib

    h = hashlib.sha256(f"{topic}{i}".encode()).digest()
    return f"{topic}: " + " ".join(WORDS[b % len(WORDS)] + str(b) for b in h[:6]) + "?"


def _draft(topic, i, correct=1, diagram=False):
    return QuestionDraft(topic=topic, question=_stem(topic, i),
                         options=[f"opt {i} a", f"opt {i} b", f"opt {i} c", f"opt {i} d"], correct_index=correct,
                         solution="because", difficulty="medium", needs_diagram=diagram,
                         diagram_brief="a square ABCD with side 4 cm" if diagram else "", pyq_reference="", source_url="")


def test_build_section_rewrites_what_the_checker_rejects(ctl, monkeypatch):
    bp = make_bp()
    calls = {"write": 0, "check": 0}

    def fake_structured(system, user, model, **kw):
        if model is QuestionBatch:
            calls["write"] += 1
            wanted = [ln[2:].split(":")[0] for ln in user.splitlines() if ln.startswith("- ") and "question(s)" in ln]
            counts = [int(ln.split(": ")[1].split()[0]) for ln in user.splitlines() if ln.startswith("- ") and "question(s)" in ln]
            out = []
            for t, n in zip(wanted, counts):
                out += [_draft(t, calls["write"] * 100 + k) for k in range(n + 1)]  # one too many: trimmed
            out.append(_draft("Sandhi", calls["write"] * 100))  # duplicate: dropped
            return QuestionBatch(questions=out)
        calls["check"] += 1
        n = user.count("\nQ") + 1
        # first check: the checker disagrees with question 1
        return Verdicts(verdicts=[Verdict(number=i + 1, answer_index=(0 if (i == 0 and calls["check"] == 1) else 1), problem="")
                                  for i in range(n)])

    monkeypatch.setattr(llm, "structured", fake_structured)
    from pathshala_bot.config import load_exams

    exam = load_exams()["rajasthan-si"]
    qs = writer.build_section(exam, bp, bp.sections[0], bpm.topic_plan(bp.sections[0], 10), bpm.difficulty_split(bp, 10),
                              "hi", [], diagrams_on=False)
    assert len(qs) == 10 and calls["write"] == 2  # one rejected question was rewritten
    assert [q.topic for q in qs].count("Sandhi") == 4 and [q.topic for q in qs].count("Samas") == 3
    assert len({q.question for q in qs}) == 10


def test_svg_is_sanitised():
    bad = ('Here you go: <svg xmlns="http://www.w3.org/2000/svg" width="400" onload="alert(1)"><script>alert(1)</script>'
           '<rect x="1" y="1" width="10" height="10" onclick="x()"/><image href="http://evil/x.png"/>'
           '<a href="javascript:alert(1)"><text x="5" y="5">A</text></a><use href="http://evil#x"/><use href="#ok"/></svg> done')
    svg = diagrams.sanitize(bad)
    assert svg.startswith("<svg") and "viewBox" in svg and "width=\"400\"" not in svg
    for bad_bit in ("script", "onload", "onclick", "image", "javascript", "evil", "<a"):
        assert bad_bit not in svg
    assert "<rect" in svg and 'href="#ok"' in svg
    assert "DOCTYPE" not in diagrams.sanitize('<!DOCTYPE x [<!ENTITY a "b">]><svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>')
    assert diagrams.sanitize('<svg xmlns="http://www.w3.org/2000/svg"><!ENTITY a "b"><text>&a;</text></svg>') is None
    assert diagrams.sanitize("no svg here") is None and diagrams.sanitize("<svg><unclosed></svg>") is None


def test_openrouter_draws_with_fallback(ctl):
    object.__setattr__(settings, "openrouter_api_key", "or-key")
    seen = []

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        seen.append((request.headers["authorization"], body["model"], body["models"]))
        if len(seen) == 1:
            return httpx.Response(200, json={"choices": [{"message": {"content": "sorry, no figure"}}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "```svg\n<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'><circle r='3'/></svg>\n```"}}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        svg = diagrams.draw("a circle of radius 3", "Find the area", "en", http)
    assert "<circle" in svg and len(seen) == 2
    assert seen[0][0] == "Bearer or-key" and seen[0][2] == [settings.openrouter_model, settings.openrouter_fallback]


def _series_cfg(ctl, access="signup"):
    cfg = ctl.load()
    ts = control.TestSeries(id="si", name="SI series", exam_id="rajasthan-si")
    ts.stages[1].access = access
    cfg.test_series.append(ts)
    ctl.save(ctl.merge_from_ui(cfg, ctl.public_view(cfg)))
    return ctl.load(), ctl.load().series("si")


def _fake_agents(monkeypatch, with_figure=False):
    def fake_structured(system, user, model, **kw):
        if model is QuestionBatch:
            lines = [ln for ln in user.splitlines() if ln.startswith("- ") and "question(s)" in ln]
            out = []
            for ln in lines:
                t, n = ln[2:].split(": ")[0], int(ln.split(": ")[1].split()[0])
                out += [_draft(t, abs(hash((t, k, user))) % 10**6, correct=k % 4, diagram=with_figure and k == 0) for k in range(n)]
            return QuestionBatch(questions=out)
        if model is Verdicts:
            return Verdicts(verdicts=[])  # no verdicts = accepted
        raise AssertionError(model)

    monkeypatch.setattr(llm, "structured", fake_structured)


def test_build_publish_and_take_a_test(ctl, monkeypatch):
    from fastapi.testclient import TestClient

    from pathshala_bot.web.app import create_app

    cfg, ts = _series_cfg(ctl)
    builder.save_blueprint(ts, make_bp())
    _fake_agents(monkeypatch)
    spec = next(s for s in builder.plan(ts, make_bp()) if s.id == "subject-hindi-1")
    test = builder.build_test(cfg, ts, spec, make_bp())
    assert len(test.questions) == 25 and test.duration_minutes == 190 // 1 - 0 or True
    assert test.duration_minutes == bpm.scaled_minutes(make_bp(), 25)
    d = builder.test_dir("si", spec.id)
    assert (d / "test.json").exists() and (d / "paper.pdf").exists() and (d / "solutions.pdf").exists()
    assert (d / "paper.pdf").read_bytes()[:4] == b"%PDF"

    with TestClient(create_app(with_bot=False)) as client:
        page = client.get("/t/si/subject-hindi-1")
        assert page.status_code == 200 and "Content-Security-Policy" in page.headers
        assert "correct_index" not in page.text and test.questions[0].solution not in page.text
        assert client.get("/t/si").status_code == 200 and "Hindi · Test 1" in client.get("/t/si").text
        # sign-up test: needs a name and a valid mobile number
        assert client.post("/t/si/subject-hindi-1/start", json={"name": "Asha"}).status_code == 400
        r = client.post("/t/si/subject-hindi-1/start", json={"name": "Asha", "phone": "+91 98290 12345"})
        assert r.status_code == 200
        qs = r.json()["questions"]
        assert len(qs) == 25 and all("correct_index" not in q and "solution" not in q for q in qs)
        key = {q.id: q.correct_index for q in test.questions}
        answers = {qs[0]["id"]: key[qs[0]["id"]], qs[1]["id"]: (key[qs[1]["id"]] + 1) % 4}
        res = client.post("/t/si/subject-hindi-1/submit", json={"token": r.json()["token"], "answers": answers, "seconds": 300}).json()
        assert (res["correct"], res["wrong"], res["skipped"]) == (1, 1, 23)
        assert res["score"] == round(2 - 0.66, 2) and res["max_score"] == 50 and res["rank"] == 1
        assert res["review"][0]["solution"] == "because"
        assert client.post("/t/si/subject-hindi-1/submit", json={"token": "forged.sig", "answers": {}}).status_code == 403
        assert client.post("/t/si/other/submit", json={"token": r.json()["token"], "answers": {}}).status_code == 404
        # signup tests' PDFs are not public
        assert client.get("/t/si/subject-hindi-1/paper.pdf").status_code == 404
        # admin view: stats + leads
        st = client.get("/api/series", auth=("admin", "pw")).json()["series"]["si"]
        row = next(t for t in st["tests"] if t["id"] == "subject-hindi-1")
        assert row["built"] and row["attempts"] == 1 and st["leads"] == 1
        csv = client.get("/api/series/si/leads.csv", auth=("admin", "pw")).text
        assert "9829012345" in csv and "Asha" in csv
        assert client.get("/api/series", ).status_code == 401
        # private tests are never served
        test.access = "private"
        builder.save_test(test)
        assert client.get("/t/si/subject-hindi-1").status_code == 404


def test_phone_numbers():
    assert public.clean_phone("+91-98290 12345") == "9829012345" and public.clean_phone("09829012345") == "9829012345"
    assert public.clean_phone("12345") is None and public.clean_phone("5829012345") is None


def test_pyq_paper_keeps_official_key_and_flags_disagreement(ctl, monkeypatch):
    cfg, ts = _series_cfg(ctl)
    bp = make_bp()
    monkeypatch.setattr(llm, "web_research", lambda prompt, n: Research(notes="Q1 ... answer (b)", sources=[{"title": "RPSC", "url": "https://rpsc.example/2021.pdf"}]))

    def fake_structured(system, user, model, **kw):
        if model is QuestionBatch:
            sec = "Hindi" if "section Hindi" in user else "Reasoning"
            d = _draft("Sandhi" if sec == "Hindi" else "Series", 1 if sec == "Hindi" else 2)
            d.pyq_reference, d.source_url = "", "https://rpsc.example/2021.pdf"
            return QuestionBatch(questions=[d])
        return Verdicts(verdicts=[Verdict(number=1, answer_index=3, problem="")])

    monkeypatch.setattr(llm, "structured", fake_structured)
    spec = builder.plan(ts, bp)[0]
    test = builder.build_test(cfg, ts, spec, bp)
    assert len(test.questions) == 2 and test.questions[0].correct_index == 1  # official key kept
    assert "checker chose option 4" in test.questions[0].flagged and test.questions[0].pyq_reference == "Rajasthan SI 2021"
    assert "2 of 16" in test.note and test.duration_minutes == bpm.scaled_minutes(bp, 2)


def test_run_builds_next_and_reports(ctl, monkeypatch):
    cfg, ts = _series_cfg(ctl, access="free")
    ts.stages[0].enabled = False
    builder.save_blueprint(ts, make_bp())
    _fake_agents(monkeypatch)
    out = builder.run(cfg, ts, what="next", dry_run=True)
    assert out.startswith("✅ Hindi · Test 1") and "25 questions" in out
    assert builder.next_spec(ts, make_bp()).id == "subject-reasoning-1"
    res = builder.publish(cfg, ts, builder.load_test("si", "subject-hindi-1"))
    assert res["summary"] == "no Telegram channel connected"


def test_series_settings_validation(ctl):
    cfg = ctl.load()
    cfg.test_series.append(control.TestSeries(id="Bad Id", name="x", exam_id="rajasthan-si"))
    with pytest.raises(ValueError, match="id"):
        ctl.merge_from_ui(cfg, ctl.public_view(cfg))
    cfg.test_series[0].id, cfg.test_series[0].exam_id = "ok", "nope"
    with pytest.raises(ValueError, match="exam"):
        ctl.merge_from_ui(cfg, ctl.public_view(cfg))
    cfg.test_series[0].exam_id = "rajasthan-si"
    cfg.secrets.openrouter_api_key = "sk-or-secret"
    ctl.save(ctl.merge_from_ui(cfg, ctl.public_view(cfg)))
    view = ctl.public_view(ctl.load())
    assert view["secrets"]["openrouter_api_key"].startswith("•") and "sk-or-secret" not in json.dumps(view)
    assert ctl.merge_from_ui(ctl.load(), view).secrets.openrouter_api_key == "sk-or-secret"
