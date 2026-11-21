"""Exams typed by name (web-researched profiles) and the previous-year-question format."""

from __future__ import annotations

from pathlib import Path

import pytest

from pathshala_bot import control, exam_research, i18n, llm
from pathshala_bot.config import load_exams, settings
from pathshala_bot.models import PYQ, ContentPackage
from pathshala_bot.providers import Research

FIXTURE = Path(__file__).parent / "fixtures" / "rajasthan_si_sample.json"


@pytest.fixture
def ctl(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTROL_FILE", str(tmp_path / "control.json"))
    saved = dict(vars(settings))
    object.__setattr__(settings, "output_dir", tmp_path)
    object.__setattr__(settings, "db_path", tmp_path / "h.sqlite3")
    yield control
    for k, v in saved.items():
        object.__setattr__(settings, k, v)
    i18n._failed.clear()
    i18n._cached_pack.cache_clear()


def _profile(**kw):
    base = dict(id="Rajasthan Patwari!", name="Rajasthan Patwari Recruitment Exam", short_name="Rajasthan Patwari", language="hi",
                audience="Graduates of Rajasthan", conducting_body="Rajasthan Staff Selection Board (RSSB)",
                pattern="150 MCQs, 300 marks, 3 hours, 1/3 negative marking",
                subjects=[exam_research.SubjectTopics(subject="Rajasthan GK", topics=["Rivers", "Forts"]),
                          exam_research.SubjectTopics(subject="Computer", topics=["MS Excel shortcuts"]),
                          exam_research.SubjectTopics(subject="Empty", topics=[])],
                sources=["https://rssb.rajasthan.gov.in/syllabus", "not a url"])
    return exam_research.ExamProfile(**{**base, **kw})


def test_typed_names_match_the_catalogue():
    exams = load_exams()
    m = exam_research.match_catalogue
    assert m("rajasthan si", exams).id == "rajasthan-si"
    assert m("JEE  main", exams).id == "jee-main"
    assert m("Rajasthan Police Sub-Inspector (RPSC SI)", exams).id == "rajasthan-si"
    assert m("Bihar Daroga", exams) is None and m("", exams) is None


def test_unknown_exam_is_researched_into_a_custom_exam(ctl, monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "web_research", lambda prompt, n: calls.append(prompt) or Research(
        notes="RSSB conducts it...", sources=[{"title": "RSSB", "url": "https://rssb.rajasthan.gov.in/notice"}]))
    monkeypatch.setattr(llm, "structured", lambda system, user, model, **k: _profile(language="xx"))
    cfg = ctl.load()
    cfg.general.default_language = "hi"
    res = exam_research.lookup(cfg, "rajasthan patwari")
    ex = res["exam"]
    assert '"rajasthan patwari"' in calls[0]
    assert ex["id"] == "rajasthan-patwari" and ex["language"] == "hi"  # unknown code -> default language
    assert ex["subjects"] == {"Rajasthan GK": ["Rivers", "Forts"], "Computer": ["MS Excel shortcuts"]}  # empty subject dropped
    assert ex["sources"] == ["https://rssb.rajasthan.gov.in/syllabus", "https://rssb.rajasthan.gov.in/notice"]
    assert ex["typed_name"] == "rajasthan patwari" and ex["researched_on"]

    # saved like any custom exam; the agent sees its pattern
    cfg.custom_exams.append(control.CustomExam.model_validate(ex))
    cfg.spaces[0].exam_ids.append(ex["id"])
    ctl.save(ctl.merge_from_ui(cfg, ctl.public_view(cfg)))
    exam = ctl.space_exams(ctl.load(), ctl.load().spaces[0])["rajasthan-patwari"]
    assert "RSSB" in exam.details and "negative marking" in exam.details

    # the same name again resolves without research; a clash gets a new id; refresh keeps the id
    assert exam_research.lookup(ctl.load(), "Rajasthan Patwari") == {"match": "rajasthan-patwari", "name": ex["name"]}
    assert exam_research.lookup(ctl.load(), "Patwari Rajasthan 2025")["exam"]["id"] == "rajasthan-patwari-2"
    assert exam_research.lookup(ctl.load(), "Rajasthan Patwari", refresh_id="rajasthan-patwari")["exam"]["id"] == "rajasthan-patwari"
    with pytest.raises(ValueError):
        exam_research.lookup(ctl.load(), " ")


def test_no_syllabus_found_is_an_error(ctl, monkeypatch):
    monkeypatch.setattr(llm, "web_research", lambda prompt, n: Research(notes="", sources=[]))
    monkeypatch.setattr(llm, "structured", lambda *a, **k: _profile(subjects=[]))
    with pytest.raises(RuntimeError, match="syllabus"):
        exam_research.lookup(ctl.load(), "Some Unknown Exam")


def test_lookup_api(ctl, monkeypatch):
    from fastapi.testclient import TestClient

    from pathshala_bot.web.app import create_app

    monkeypatch.setenv("ADMIN_PASSWORD", "pw")
    seen = {}

    def fake_lookup(cfg, name, refresh_id=None):
        seen.update(name=name, customs=[c.id for c in cfg.custom_exams])
        if name == "boom":
            raise RuntimeError("no API key")
        return {"exam": {"id": "bihar-daroga"}}

    monkeypatch.setattr(exam_research, "lookup", fake_lookup)
    unsaved = {"id": "my-exam", "name": "Mine", "short_name": "Mine", "language": "hi", "subjects": {"A": ["b"]}}
    with TestClient(create_app(with_bot=False)) as client:
        r = client.post("/api/exams/lookup", json={"name": "Bihar Daroga", "custom_exams": [unsaved]}, auth=("admin", "pw"))
        assert r.json() == {"exam": {"id": "bihar-daroga"}}
        assert seen == {"name": "Bihar Daroga", "customs": ["my-exam"]}  # unsaved browser exams count as known
        r = client.post("/api/exams/lookup", json={"name": "boom"}, auth=("admin", "pw"))
        assert r.status_code == 502 and "no API key" in r.json()["detail"]


# ---------------- previous-year question ----------------

def _pyq_package(with_pyq=True) -> ContentPackage:
    pkg = ContentPackage.model_validate_json(FIXTURE.read_text(encoding="utf-8"))
    pkg.content_type = "previous_year_question"
    pkg.pyq = PYQ(exam_name="RPSC SI", year="2021", question="राजस्थान का एकीकरण कितने चरणों में हुआ?",
                  options=["5", "6", "7", "8"], correct_index=2, solution="18 मार्च 1948 से 1 नवंबर 1956 तक सात चरण।",
                  source_url="https://rpsc.rajasthan.gov.in/answerkey") if with_pyq else None
    return pkg


def test_pyq_short_opens_with_exam_and_year():
    exam = load_exams()["rajasthan-si"]
    pkg = llm.apply_pyq(_pyq_package(), exam)
    first = pkg.short.scenes[0]
    assert first.kind == "title" and first.heading == "RPSC SI · 2021"
    assert first.narration == "दोस्तों, यह प्रश्न साल 2021 में RPSC SI परीक्षा में पूछा गया था..."
    quiz = next(s for s in pkg.short.scenes if s.kind == "quiz")
    assert quiz.body.startswith("राजस्थान का एकीकरण") and quiz.options == ["5", "6", "7", "8"] and quiz.correct_index == 2
    reveal = pkg.short.scenes[2]
    assert reveal.kind == "reveal" and reveal.correct_index == 2 and reveal.heading == "उत्तर: 7" and "सात चरण" in reveal.narration
    assert pkg.mcqs[0].question.startswith("राजस्थान का एकीकरण") and pkg.mcqs[0].pyq_reference == "RPSC SI 2021"
    assert len(pkg.mcqs) <= 5 and pkg.notes[0].heading.endswith("RPSC SI 2021") and "✔ 7" in pkg.notes[0].bullets
    assert pkg.sources[0].url == "https://rpsc.rajasthan.gov.in/answerkey"


def test_pyq_opener_languages(ctl, monkeypatch):
    from dataclasses import replace

    exam = replace(load_exams()["rajasthan-si"], language="hinglish")
    pkg = llm.apply_pyq(_pyq_package(), exam)
    assert pkg.short.scenes[0].narration == "Dosto, yeh prashan saal 2021 me RPSC SI exam me poocha gaya tha..."
    assert i18n.pyq_opener("en", "JEE Main", "2023") == "Friends, this question was asked in the JEE Main exam in 2023..."
    # other languages: translated once like every label; an old cached pack without the new label is refreshed
    cache = settings.output_dir / "cache" / "i18n" / "ta.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text('{"key_points": "முக்கிய குறிப்புகள்"}', encoding="utf-8")
    monkeypatch.setattr(i18n, "translate_pack", lambda code: {**i18n._english_full(), "pyq_opener": "நண்பர்களே, இந்தக் கேள்வி {year} ஆம் ஆண்டு {exam} தேர்வில் கேட்கப்பட்டது..."})
    i18n._cached_pack.cache_clear()
    assert i18n.pyq_opener("ta", "TNPSC Group 4", "2022") == "நண்பர்களே, இந்தக் கேள்வி 2022 ஆம் ஆண்டு TNPSC Group 4 தேர்வில் கேட்கப்பட்டது..."


def test_without_verified_pyq_it_becomes_a_pyq_style_quiz():
    exam = load_exams()["rajasthan-si"]
    pkg = llm.apply_pyq(_pyq_package(with_pyq=False), exam)
    assert pkg.content_type == "pyq_quiz" and pkg.pyq is None
    other = _pyq_package()
    other.content_type = "cheatsheet"
    assert llm.apply_pyq(other, exam).pyq is None  # only for its own format


def test_pyq_research_prompt_and_style(monkeypatch):
    import random

    from pathshala_bot import pipeline

    exam = load_exams()["rajasthan-si"]
    prompts = []
    monkeypatch.setattr(llm, "web_research", lambda prompt, n: prompts.append(prompt) or Research(notes="", sources=[]))
    llm.research_topic(exam, "Rajasthan History & Culture", "Integration of Rajasthan", "previous_year_question", [])
    llm.research_topic(exam, "Rajasthan History & Culture", "Integration of Rajasthan", "cheatsheet", [])
    assert "NO VERIFIED PYQ FOUND" in prompts[0] and "NO VERIFIED PYQ FOUND" not in prompts[1]
    assert pipeline.pick_style(exam, "Rajasthan History & Culture", "previous_year_question", random.Random(0)) == "pyq_reveal"


def test_pipeline_forces_pyq_reel(ctl, monkeypatch):
    from pathshala_bot import pipeline

    seen = {}

    def stop(exam, subject, topic, ctype, avoid):
        raise RuntimeError("stop after planning")

    monkeypatch.setattr(llm, "research_topic", lambda *a: seen.update(args=a) or stop(*a))
    res = pipeline.run(exam_id="rajasthan-si", content_type="previous_year_question", video_style="map_story", make_video=False)
    assert seen["args"][3] == "previous_year_question" and "research" in res.errors
    assert "previous_year_question" in res.run_dir.name


def test_telegram_poll_shows_exam_and_year(ctl, monkeypatch):
    from pathshala_bot.publish import telegram

    polls = []
    monkeypatch.setattr(telegram.Telegram, "call", lambda self, method, data=None, files=None, json=None: (
        polls.append(json or data) if method == "sendPoll" else None) or {"message_id": 1})
    monkeypatch.setattr("time.sleep", lambda s: None)
    object.__setattr__(settings, "telegram_bot_token", "1:x")
    exam = load_exams()["rajasthan-si"]
    pkg = llm.apply_pyq(_pyq_package(), exam)
    telegram.publish(pkg, exam, {}, chat_id="@t", include={"questions"})
    assert polls and str(polls[0].get("question", "")).startswith("[RPSC SI 2021] राजस्थान का एकीकरण")


def test_pyq_question_follows_the_opener():
    pkg = llm.apply_pyq(_pyq_package(), load_exams()["rajasthan-si"])
    kinds = [s.kind for s in pkg.short.scenes]
    assert kinds[:3] == ["title", "quiz", "reveal"] and kinds[-1] == "cta"
