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


def access_token() -> str:
    r = httpx.post(
        TOKEN_URL,
        data={
            "client_id": settings.youtube_client_id,
            "client_secret": settings.youtube_client_secret,
            "refresh_token": settings.youtube_refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def publish(pkg: ContentPackage, exam: Exam, video: Path) -> dict:
    if not settings.youtube_enabled:
        raise RuntimeError("YouTube credentials are not configured")
    token = access_token()
    title = pkg.short.title if "#shorts" in pkg.short.title.lower() else f"{pkg.short.title} #Shorts"
    hashtags = " ".join(f"#{h}" for h in pkg.short.hashtags)
    description = (
        f"{pkg.short.caption}\n\n"
        + "\n".join(f"✅ {p}" for p in pkg.key_points[:5])
        + f"\n\n📚 Free PDF notes & daily quizzes on Telegram: {settings.brand_handle}\n\n{hashtags} #Shorts"
    )
    meta = {
        "snippet": {
            "title": title[:100],
            "description": description[:4900],
            "tags": [h for h in pkg.short.hashtags][:15] + [exam.short_name],
            "categoryId": EDUCATION_CATEGORY,
            "defaultLanguage": "hi" if exam.language == "hi" else "en",
            "defaultAudioLanguage": "hi" if exam.language in ("hi", "hinglish") else "en",
        },
        "status": {"privacyStatus": settings.youtube_privacy, "selfDeclaredMadeForKids": False},
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
