"""Offline tests: no Claude / Telegram / Google calls."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pathshala_bot import pipeline
from pathshala_bot.config import load_exams
from pathshala_bot.llm import sanitize
from pathshala_bot.models import ContentPackage
from pathshala_bot.publish import telegram
from pathshala_bot.render import images

FIXTURE = Path(__file__).parent / "fixtures" / "rajasthan_si_sample.json"


@pytest.fixture
def pkg() -> ContentPackage:
    return ContentPackage.model_validate_json(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def exam():
    return load_exams()["rajasthan-si"]


def test_exams_load():
    exams = load_exams()
    assert {"rajasthan-si", "jee-main", "school-cbse-10"} <= set(exams)
    for e in exams.values():
        assert e.subjects and all(e.subjects.values())
        assert e.language in {"hi", "en", "hinglish"}


def test_pick_type_respects_subject():
    import random

    exams = load_exams()
    rng = random.Random(1)
    types = {pipeline.pick_type(exams["neet-ug"], "Biology", rng) for _ in range(200)}
    assert "formula_sheet" not in types and "current_affairs" not in types
    assert pipeline.pick_type(exams["rajasthan-si"], "Current Affairs (Rajasthan & India)", rng) == "current_affairs"


def test_sanitize_enforces_telegram_limits(pkg):
    q = pkg.mcqs[0]
    q.question = "x" * 500
    q.options = ["a" * 150, "b", "", "d"]
    q.explanation = "e" * 400
    bad = q.model_copy(update={"correct_index": 9, "question": "bad"})
    pkg.mcqs.append(bad)
    pkg.short.hashtags = ["#Rajasthan SI", "RPSC"]
    out = sanitize(pkg)
    assert len(out.mcqs[0].question) == 300
    assert len(out.mcqs[0].options) == 3 and len(out.mcqs[0].options[0]) == 100
    assert len(out.mcqs[0].explanation) == 200
    assert all(m.question != "bad" for m in out.mcqs)
    assert out.short.hashtags == ["RajasthanSI", "RPSC"]


def test_safe_html_keeps_only_allowed_tags():
    assert telegram.safe_html("<b>bold</b> <script>x</script> a<b") == "<b>bold</b> &lt;script&gt;x&lt;/script&gt; a&lt;b"


def test_render_card_and_slides(pkg, exam, tmp_path):
    card = images.render_card(pkg, exam, tmp_path / "card.png")
    slides = images.render_slides(pkg, exam, tmp_path / "slides")
    assert card.stat().st_size > 10_000
    assert len(slides) == len(pkg.short.slides)
    from PIL import Image

    assert Image.open(slides[0]).size == (1080, 1920)


def test_render_pdf(pkg, exam, tmp_path):
    pytest.importorskip("weasyprint")
    from pathshala_bot.render import pdf

    out = pdf.render_pdf(pkg, exam, tmp_path / "notes.pdf")
    assert out.read_bytes()[:4] == b"%PDF"


def test_telegram_publish_sequence(pkg, exam, tmp_path, monkeypatch):
    calls = []

    def fake_call(self, method, data=None, files=None, json=None):
        calls.append((method, data or json))
        return {"message_id": len(calls)}

    monkeypatch.setattr(telegram.Telegram, "call", fake_call)
    monkeypatch.setattr(telegram.time, "sleep", lambda s: None)
    card = images.render_card(pkg, exam, tmp_path / "card.png")
    pdf_path = tmp_path / "notes.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")
    tg = telegram.Telegram(token="123:abc")
    monkeypatch.setattr(telegram, "Telegram", lambda: tg)

    res = telegram.publish(pkg, exam, {"card": card, "pdf": pdf_path}, chat_id="@test")
    methods = [c[0] for c in calls]
    assert methods[0] == "sendPhoto"
    assert methods.count("sendPoll") == len(pkg.mcqs)
    assert methods[-1] == "sendDocument"
    poll = next(c[1] for c in calls if c[0] == "sendPoll")
    assert poll["type"] == "quiz" and len(poll["options"]) == 4
    assert res["chat_id"] == "@test"


def test_pipeline_from_json_dry_run(tmp_path, monkeypatch):
    from pathshala_bot import config

    monkeypatch.setattr(config, "settings", config.Settings(output_dir=tmp_path, db_path=tmp_path / "h.sqlite3"))
    monkeypatch.setattr(pipeline, "settings", config.settings)
    res = pipeline.run(exam_id="rajasthan-si", from_json=FIXTURE, make_video=False)
    assert res.ok
    manifest = json.loads((res.run_dir / "manifest.json").read_text())
    assert manifest["topic"] and "card" in manifest["assets"]
