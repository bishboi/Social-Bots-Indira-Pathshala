"""Voice-over: ElevenLabs (best Hindi), Microsoft Edge TTS (free), or none (silent video)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx

from ..config import settings

log = logging.getLogger(__name__)


def _elevenlabs(text: str, out: Path) -> None:
    headers = {"Accept": "audio/mpeg"}
    if settings.elevenlabs_api_key:
        headers["xi-api-key"] = settings.elevenlabs_api_key
    r = httpx.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{settings.elevenlabs_voice_id}",
        params={"output_format": "mp3_44100_128"},
        headers=headers,
        json={
            "text": text,
            "model_id": settings.elevenlabs_model,
            "voice_settings": {"stability": 0.45, "similarity_boost": 0.8, "style": 0.3},
        },
        timeout=120,
    )
    r.raise_for_status()
    out.write_bytes(r.content)


def _edge(text: str, out: Path, language: str) -> None:
    import edge_tts  # optional dependency

    voice = settings.edge_voice_hi if language in ("hi", "hinglish") else settings.edge_voice_en
    asyncio.run(edge_tts.Communicate(text, voice, rate="+8%").save(str(out)))


def providers() -> list[str]:
    p = settings.tts_provider.lower()
    if p == "none":
        return []
    if p in ("elevenlabs", "edge"):
        return [p]
    return ["elevenlabs", "edge"]


class TTS:
    """Synthesises clips with the first provider that works and sticks with it for the run."""

    def __init__(self, language: str):
        self.language = language
        self.order = providers()

    def synth(self, text: str, out: Path) -> Path | None:
        text = text.strip()
        if not text:
            return None
        while self.order:
            name = self.order[0]
            try:
                if name == "elevenlabs":
                    _elevenlabs(text, out)
                else:
                    _edge(text, out, self.language)
                if out.exists() and out.stat().st_size > 1000:
                    return out
                raise RuntimeError("empty audio")
            except Exception as e:  # provider not configured / network / quota
                log.warning("TTS provider %s failed (%s); trying next", name, e)
                self.order.pop(0)
        return None
