"""YouTube Shorts upload via the YouTube Data API v3 resumable upload.

Needs an OAuth client (Desktop app) + a refresh token with the
`youtube.upload` scope; get one with `python scripts/youtube_auth.py`.
A video counts as a Short when it is vertical and <= 60s; "#Shorts" in the
title/description helps classification.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from ..config import Exam, settings
from ..models import ContentPackage

log = logging.getLogger(__name__)

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
EDUCATION_CATEGORY = "27"


def _audio_language(code: str) -> str:
    """Language of the voice-over (a borrowed voice reads e.g. Sanskrit with Hindi)."""
    from ..languages import LANGUAGES

    lang = LANGUAGES.get(code)
    if code == "hinglish":
        return "hi"
    if lang and lang.voice_fallback and not (lang.edge_voice or lang.eleven_model):
        return lang.voice_fallback
    return code


def default_creds() -> dict:
    return {"client_id": settings.youtube_client_id, "client_secret": settings.youtube_client_secret,
            "refresh_token": settings.youtube_refresh_token, "privacy": privacy}


def access_token(creds: dict | None = None) -> str:
    c = creds or default_creds()
    r = httpx.post(
        TOKEN_URL,
        data={
            "client_id": c["client_id"],
            "client_secret": c["client_secret"],
            "refresh_token": c["refresh_token"],
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def publish(pkg: ContentPackage, exam: Exam, video: Path, credits: list[str] | None = None, creds: dict | None = None) -> dict:
    creds = creds or default_creds()
    if not (creds.get("client_id") and creds.get("client_secret") and creds.get("refresh_token")):
        raise RuntimeError("YouTube credentials are not configured")
    privacy = creds.get("privacy") or settings.youtube_privacy
    token = access_token(creds)
    title = pkg.short.title if "#shorts" in pkg.short.title.lower() else f"{pkg.short.title} #Shorts"
    hashtags = " ".join(f"#{h}" for h in pkg.short.hashtags)
    description = (
        f"{pkg.short.caption}\n\n"
        + "\n".join(f"✅ {p}" for p in pkg.key_points[:5])
        + f"\n\n📚 Free PDF notes & daily quizzes on Telegram: {settings.brand_handle}\n\n{hashtags} #Shorts"
    )
    if any(sc.kind == "map" for sc in pkg.short.scenes):
        from ..geo import ATTRIBUTION

        description += f"\n\n{ATTRIBUTION}"
    if credits:
        description += "\nImages: " + "; ".join(credits)
    meta = {
        "snippet": {
            "title": title[:100],
            "description": description[:4900],
            "tags": [h for h in pkg.short.hashtags][:15] + [exam.short_name],
            "categoryId": EDUCATION_CATEGORY,
            "defaultLanguage": {"hinglish": "hi"}.get(exam.language, exam.language),
            "defaultAudioLanguage": _audio_language(exam.language),
        },
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    size = video.stat().st_size
    with httpx.Client(timeout=httpx.Timeout(60, write=600, read=600)) as http:
        init = http.post(
            UPLOAD_URL,
            params={"uploadType": "resumable", "part": "snippet,status"},
            headers={
                "Authorization": f"Bearer {token}",
                "X-Upload-Content-Type": "video/mp4",
                "X-Upload-Content-Length": str(size),
            },
            json=meta,
        )
        init.raise_for_status()
        session_url = init.headers["Location"]
        with video.open("rb") as f:
            up = http.put(session_url, content=f.read(), headers={"Authorization": f"Bearer {token}", "Content-Type": "video/mp4"})
        up.raise_for_status()
        vid = up.json()["id"]
    log.info("YouTube upload ok: %s", vid)
    return {"video_id": vid, "url": f"https://youtube.com/shorts/{vid}", "privacy": settings.youtube_privacy}
