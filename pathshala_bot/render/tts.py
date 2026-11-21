"""Voice-over: Gemini TTS (gemini-3.8-flash-lite-tts), Microsoft Edge TTS (free) as backup, or none (silent).

ElevenLabs is still available when TTS_PROVIDER=elevenlabs is chosen explicitly.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx

from ..config import settings

log = logging.getLogger(__name__)


def voice_plan(language: str) -> dict:
    """Which ElevenLabs model and Edge voice can read a language (or a related one, approximately).

    eleven_multilingual_v2 covers Hindi, Tamil and English; eleven_v3 covers Assamese, Bengali, Gujarati,
    Kannada, Malayalam, Marathi, Nepali, Odia, Punjabi, Telugu and Urdu. Edge has Indian voices for
    bn, gu, hi, kn, ml, mr, ne, ta, te, ur (+ English). Languages with no voice of their own borrow one from
    a language that reads the same script (e.g. Sanskrit/Maithili/Dogri/Bodo -> Hindi, Kashmiri/Sindhi -> Urdu);
    Santali (Ol Chiki) and Manipuri (Meitei Mayek) have none, so their videos are silent.
    """
    from ..languages import LANGUAGES

    lang = LANGUAGES.get(language) or LANGUAGES["en"]
    donor = LANGUAGES.get(lang.voice_fallback) if lang.voice_fallback else None
    eleven = lang.eleven_model or (donor.eleven_model if donor else "")
    if eleven == "eleven_multilingual_v2" and settings.elevenlabs_model and language in ("hi", "en", "hinglish"):
        eleven = settings.elevenlabs_model  # ELEVENLABS_MODEL keeps applying to Hindi/English
    gem = lang.gemini or (donor.gemini if donor else "")
    gemini_model = {"lite": settings.gemini_tts_model, "flash": settings.gemini_tts_model_full}.get(gem, "")
    edge = lang.edge_voice or (donor.edge_voice if donor else "")
    if language in ("hi", "hinglish"):
        edge = settings.edge_voice_hi
    elif language == "en":
        edge = settings.edge_voice_en
    native = lang.gemini or lang.eleven_model or lang.edge_voice
    return {"gemini_model": gemini_model, "eleven_model": eleven, "edge_voice": edge, "approximate": bool(donor and not native)}


GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _gemini(text: str, out: Path, language: str) -> None:
    """Gemini TTS: returns a WAV (24 kHz mono 16-bit); converted to MP3 for the video pipeline."""
    import base64
    import subprocess

    model = voice_plan(language)["gemini_model"]
    if not model:
        raise RuntimeError(f"no Gemini TTS model for language '{language}'")
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    body = {
        "contents": [{"role": "user", "parts": [{"text": text, "speech_metadata": {"style": settings.gemini_tts_style}}]}],
        "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {"voiceConfig": {"voice": settings.gemini_tts_voice}}},
    }
    r = httpx.post(GEMINI_URL.format(model=model), headers={"x-goog-api-key": settings.gemini_api_key}, json=body, timeout=180)
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", {}).get("message", r.text[:300])
        except ValueError:
            msg = r.text[:300]
        raise RuntimeError(f"Gemini TTS {r.status_code}: {msg}")
    data = r.json()
    try:
        part = next(p for p in data["candidates"][0]["content"]["parts"] if "inlineData" in p or "inline_data" in p)
    except (KeyError, IndexError, StopIteration):
        raise RuntimeError(f"Gemini TTS returned no audio: {str(data)[:300]}") from None
    inline = part.get("inlineData") or part.get("inline_data")
    audio = base64.b64decode(inline["data"])
    wav = out.with_suffix(".wav")
    if audio[:4] == b"RIFF":
        wav.write_bytes(audio)
    else:  # raw 16-bit PCM (audio/l16) -> add a WAV header
        import wave

        rate = 24000
        mime = inline.get("mimeType") or inline.get("mime_type") or ""
        if "rate=" in mime:
            rate = int(mime.split("rate=")[-1].split(";")[0])
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(audio)
    proc = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-codec:a", "libmp3lame", "-q:a", "3", str(out)],
                          capture_output=True, text=True)
    wav.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError(f"could not convert Gemini audio: {proc.stderr[-300:]}")


def _elevenlabs(text: str, out: Path, language: str = "hi") -> None:
    model = voice_plan(language)["eleven_model"]
    if not model:
        raise RuntimeError(f"no ElevenLabs model for language '{language}'")
    headers = {"Accept": "audio/mpeg"}
    if settings.elevenlabs_api_key:
        headers["xi-api-key"] = settings.elevenlabs_api_key
    r = httpx.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{settings.elevenlabs_voice_id}",
        params={"output_format": "mp3_44100_128"},
        headers=headers,
        json={
            "text": text,
            "model_id": model,
            "voice_settings": {"stability": 0.45, "similarity_boost": 0.8, "style": 0.3},
        },
        timeout=120,
    )
    r.raise_for_status()
    out.write_bytes(r.content)


def _edge(text: str, out: Path, language: str) -> None:
    import edge_tts  # optional dependency

    voice = voice_plan(language)["edge_voice"]
    if not voice:
        raise RuntimeError(f"no Edge voice for language '{language}'")
    asyncio.run(edge_tts.Communicate(text, voice, rate="+8%").save(str(out)))


def providers() -> list[str]:
    p = settings.tts_provider.lower()
    if p == "none":
        return []
    if p in ("gemini", "elevenlabs", "edge"):
        return [p] if p == "edge" else [p, "edge"]  # Edge (free) stays as the backup
    return ["gemini", "edge"]


class TTS:
    """Synthesises clips with the first provider that works and sticks with it for the run."""

    def __init__(self, language: str):
        self.language = language
        plan = voice_plan(language)
        need = {"gemini": "gemini_model", "elevenlabs": "eleven_model", "edge": "edge_voice"}
        self.order = [p for p in providers() if plan[need[p]]]
        if not self.order and providers():
            log.warning("no voice available for language '%s' - the short will be silent", language)
        elif plan["approximate"]:
            log.info("no native voice for '%s'; reading it with a related language's voice", language)

    def synth(self, text: str, out: Path) -> Path | None:
        text = text.strip()
        if not text:
            return None
        while self.order:
            name = self.order[0]
            try:
                if name == "gemini":
                    _gemini(text, out, self.language)
                elif name == "elevenlabs":
                    _elevenlabs(text, out, self.language)
                else:
                    _edge(text, out, self.language)
                if out.exists() and out.stat().st_size > 1000:
                    return out
                raise RuntimeError("empty audio")
            except Exception as e:  # provider not configured / network / quota
                log.warning("TTS provider %s failed (%s); trying next", name, e)
                self.order.pop(0)
        return None
