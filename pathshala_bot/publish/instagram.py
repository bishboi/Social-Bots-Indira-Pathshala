"""Instagram Reels via the Instagram Graph API (professional account linked to a Facebook Page).

Default path uploads the local MP4 directly with the resumable upload API
(no public hosting needed). If MEDIA_PUBLIC_BASE_URL is set, Instagram pulls
the video from <base>/<run-dir-name>/short.mp4 instead.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import httpx

from ..config import Exam, settings
from ..models import ContentPackage

log = logging.getLogger(__name__)


def _graph() -> str:
    return f"https://graph.facebook.com/{settings.instagram_graph_version}"


def _check(r: httpx.Response) -> dict:
    body = r.json()
    if r.status_code >= 400 or "error" in body:
        raise RuntimeError(f"Instagram API error: {body.get('error', body)}")
    return body


def caption_for(pkg: ContentPackage) -> str:
    tags = " ".join(f"#{h}" for h in pkg.short.hashtags)
    points = "\n".join(f"✅ {p}" for p in pkg.key_points[:4])
    text = f"{pkg.short.title}\n\n{pkg.short.caption}\n\n{points}\n\n📚 Free PDF notes on Telegram: {settings.brand_handle}\n.\n{tags}"
    return text[:2200]


def publish(pkg: ContentPackage, exam: Exam, video: Path, cover: Path | None = None, credits: list[str] | None = None,
            creds: dict | None = None) -> dict:
    creds = creds or {"user_id": settings.instagram_user_id, "access_token": settings.instagram_access_token}
    token, user = creds.get("access_token"), creds.get("user_id")
    if not (token and user):
        raise RuntimeError("Instagram credentials are not configured")
    caption = caption_for(pkg)
    if credits:
        caption = (caption + "\n\nImages: " + "; ".join(credits))[:2200]
    params = {"media_type": "REELS", "caption": caption, "share_to_feed": "true", "access_token": token}

    with httpx.Client(timeout=httpx.Timeout(60, write=600, read=600)) as http:
        if settings.media_public_base_url:
            params["video_url"] = f"{settings.media_public_base_url.rstrip('/')}/{video.parent.name}/{video.name}"
            container = _check(http.post(f"{_graph()}/{user}/media", data=params))["id"]
        else:
            params["upload_type"] = "resumable"
            container = _check(http.post(f"{_graph()}/{user}/media", data=params))["id"]
            data = video.read_bytes()
            up = http.post(
                f"https://rupload.facebook.com/ig-api-upload/{settings.instagram_graph_version}/{container}",
                headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(len(data))},
                content=data,
            )
            _check(up)

        # Wait for Instagram to transcode.
        for _ in range(60):
            st = _check(http.get(f"{_graph()}/{container}", params={"fields": "status_code,status", "access_token": token}))
            code = st.get("status_code")
            if code == "FINISHED":
                break
            if code in ("ERROR", "EXPIRED"):
                raise RuntimeError(f"Instagram processing failed: {st}")
            time.sleep(10)
        else:
            raise RuntimeError("Instagram processing timed out")

        media = _check(http.post(f"{_graph()}/{user}/media_publish", data={"creation_id": container, "access_token": token}))["id"]
        link = _check(http.get(f"{_graph()}/{media}", params={"fields": "permalink", "access_token": token})).get("permalink")
    log.info("Instagram reel published: %s", link or media)
    return {"media_id": media, "url": link}
