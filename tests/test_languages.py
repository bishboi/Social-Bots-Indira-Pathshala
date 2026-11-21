"""All Indian languages: registry, fonts, labels, voice-over (Gemini TTS), rendering in other scripts."""

from __future__ import annotations

import base64
import io
import json
import wave
from datetime import date

import pytest

from pathshala_bot import i18n, languages
from pathshala_bot.config import settings

@pytest.fixture(autouse=True)
def _restore_settings():
    saved = dict(vars(settings))
    yield
    for k, v in saved.items():
        object.__setattr__(settings, k, v)
    i18n._failed.clear()
    i18n._cached_pack.cache_clear()


SCHEDULED = {"as", "bn", "brx", "doi", "gu", "hi", "kn", "ks", "kok", "mai", "ml", "mni", "mr", "ne", "or", "pa", "sa", "sat",
             "sd", "ta", "te", "ur"}  # the 22 languages of the Eighth Schedule


def test_all_scheduled_languages_registered():
    assert SCHEDULED <= set(languages.LANGUAGES)
    assert {"en", "hinglish"} <= set(languages.LANGUAGES)
    for code in SCHEDULED:
        lang = languages.get(code)
        assert lang.script in languages.SCRIPTS and lang.native and code in lang.ai_rule() or lang.name in lang.ai_rule()
    assert languages.get("ur").rtl and languages.get("sd").rtl and not languages.get("ta").rtl
    assert languages.get("oriya").code == "or"


def test_script_detection():
    d = languages.detect_script
    assert d("தமிழ் text") == "taml" and d("اردو") == "arab" and d("ᱥᱟᱱᱛᱟᱲᱤ") == "olck" and d("hello") == "latin"
    assert d("বাংলা") == "beng" and d("ꯃꯤꯇꯩ") == "mtei" and d("हिन्दी") == "deva"


def test_every_language_has_fonts_for_its_script():
    from fontTools.ttLib import TTFont

    from pathshala_bot.render import fonts

    for code, lang in languages.LANGUAGES.items():
        sample = lang.native if lang.script != "latin" else "Abc"
        for role in ("regular", "bold", "heavy", "serif", "head", "display"):
            path = fonts.for_language(code, role)
            cmap = TTFont(str(path), lazy=True).getBestCmap()
            missing = [c for c in sample if c.strip() and ord(c) not in cmap and not 0x200B <= ord(c) <= 0x200D]
            assert not missing, f"{code}/{role}: {path.name} lacks {missing}"
    assert fonts.for_language("ur", "serif").name.startswith("NotoNastaliqUrdu")  # Urdu reading text in Nastaliq
    assert fonts.for_language("ur", "regular").name.startswith("NotoNaskhArabic")  # Naskh on slides


def test_labels_fallback_cache_and_retry_pause(tmp_path, monkeypatch):
    object.__setattr__(settings, "output_dir", tmp_path)
    i18n._failed.clear()
    i18n._cached_pack.cache_clear()
    calls = []

    def failing(code):
        calls.append(code)
        raise RuntimeError("no key")

    monkeypatch.setattr(i18n, "translate_pack", failing)
    assert i18n.labels("ta")["key_points"] == "Key Points"  # English fallback
    i18n.labels("ta")
    assert calls == ["ta"]  # not retried on every lookup

    i18n._failed.clear()
    monkeypatch.setattr(i18n, "translate_pack", lambda code: {**i18n._english_full(), "key_points": "முக்கிய குறிப்புகள்", "edition": "{region} பதிப்பு",
                                                               "region_tamil_nadu": "தமிழ்நாடு"})
    assert i18n.t("ta", "key_points") == "முக்கிய குறிப்புகள்"
    assert (tmp_path / "cache" / "i18n" / "ta.json").exists()
    from pathshala_bot.akhbar import Desk

    assert Desk("Tamil Nadu", "ta").label() == "தமிழ்நாடு பதிப்பு"
    assert i18n.t("hi", "edition", region="राजस्थान") == "राजस्थान संस्करण"


def test_dates_from_cldr():
    d = date(2026, 11, 21)
    assert i18n.format_date(d, "ta").startswith("சனி")
    assert "নভেম্বর" in i18n.format_date(d, "bn")
    assert "نومبر" in i18n.format_date(d, "ur")
    assert i18n.format_date(d, "en").startswith("Saturday")


def _wav_bytes(seconds=0.3, rate=24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x01" * int(rate * seconds))
    return buf.getvalue()


def test_gemini_tts_request_and_wav_decoding(tmp_path, monkeypatch):
    import httpx

    from pathshala_bot.render import tts

    object.__setattr__(settings, "gemini_api_key", "g-test")
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.update(url=url, headers=headers, body=json)
        payload = {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "audio/wav", "data": base64.b64encode(_wav_bytes()).decode()}}]}}]}
        return httpx.Response(200, json=payload, request=httpx.Request("POST", url))

    monkeypatch.setattr(tts.httpx, "post", fake_post)
    out = tmp_path / "vo.mp3"
    tts._gemini("வணக்கம் மாணவர்களே", out, "ta")
    assert out.exists() and out.stat().st_size > 500 and not out.with_suffix(".wav").exists()
    assert seen["url"].endswith("/v1beta/models/gemini-3.8-flash-lite-tts:generateContent")
    assert seen["headers"]["x-goog-api-key"] == "g-test"
    cfg = seen["body"]["generationConfig"]
    assert cfg["responseModalities"] == ["AUDIO"] and cfg["speechConfig"]["voiceConfig"]["voice"] == settings.gemini_tts_voice
    assert seen["body"]["contents"][0]["parts"][0]["text"] == "வணக்கம் மாணவர்களே"

    # raw PCM (audio/l16) is wrapped into a WAV before conversion
    def raw_post(url, headers=None, json=None, timeout=None):
        pcm = b"\x00\x01" * 24000
        payload = {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "audio/l16;rate=24000", "data": base64.b64encode(pcm).decode()}}]}}]}
        return httpx.Response(200, json=payload, request=httpx.Request("POST", url))

    monkeypatch.setattr(tts.httpx, "post", raw_post)
    out2 = tmp_path / "vo2.mp3"
    tts._gemini("नमस्ते", out2, "hi")
    assert out2.stat().st_size > 500

    # Sindhi uses the full Flash TTS model; Urdu has no Gemini voice (Edge reads it)
    assert tts.voice_plan("sd")["gemini_model"] == settings.gemini_tts_model_full
    assert tts.voice_plan("ur")["gemini_model"] == "" and tts.voice_plan("ur")["edge_voice"].startswith("ur-IN")


def test_tts_falls_back_to_edge_when_gemini_fails(tmp_path, monkeypatch):
    from pathshala_bot.render import tts

    object.__setattr__(settings, "tts_provider", "auto")
    monkeypatch.setattr(tts, "_gemini", lambda *a: (_ for _ in ()).throw(RuntimeError("quota")))

    def fake_edge(text, out, language):
        out.write_bytes(b"x" * 2000)

    monkeypatch.setattr(tts, "_edge", fake_edge)
    engine = tts.TTS("bn")
    assert engine.order == ["gemini", "edge"]
    assert engine.synth("বাংলা", tmp_path / "a.mp3") is not None and engine.order == ["edge"]
    assert tts.TTS("ur").order == ["edge"]  # no Gemini voice listed for Urdu


@pytest.mark.parametrize("code", ["ta", "ur", "bn"])
def test_pdf_and_slides_render_in_other_scripts(code, tmp_path, monkeypatch):
    pytest.importorskip("weasyprint")
    from dataclasses import replace

    from pathshala_bot.config import load_exams
    from pathshala_bot.models import ContentPackage
    from pathshala_bot.render import images, pdf

    object.__setattr__(settings, "output_dir", tmp_path)
    monkeypatch.setattr(i18n, "translate_pack", lambda c: (_ for _ in ()).throw(RuntimeError("offline")))
    i18n._failed.clear()
    text = {"ta": "தமிழ் மாதிரி உரை", "ur": "اردو نمونہ متن", "bn": "বাংলা নমুনা লেখা"}[code]
    pkg = ContentPackage.model_validate_json(open("tests/fixtures/rajasthan_si_sample.json", encoding="utf-8").read())
    pkg.title = pkg.topic = text
    pkg.key_points = [text] * 3
    exam = replace(load_exams()["rajasthan-si"], language=code)
    out = pdf.render_pdf(pkg, exam, tmp_path / "notes.pdf")
    html = out.with_suffix(".html").read_text(encoding="utf-8")
    assert out.read_bytes()[:4] == b"%PDF" and (('dir="rtl"' in html) == (code == "ur"))
    card = images.render_card(pkg, exam, tmp_path / "card.png")
    assert card.stat().st_size > 10_000
