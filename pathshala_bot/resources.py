"""Freely licensed media + reference data from Wikipedia / Wikimedia Commons.

- wiki_summary(title): coordinates, description and lead image of an article (cached).
- fetch_image(title, dest): downloads the article's lead photo only if it is hosted on
  Commons under a free licence (CC0 / public domain / CC BY / CC BY-SA) and returns the
  attribution line that must accompany it. Non-free (fair-use) images are refused.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

import httpx

from .config import settings

log = logging.getLogger(__name__)

FREE_LICENSE = re.compile(r"^(cc0|public domain|pd|cc by(-sa)? \d)", re.I)
_lock = threading.Lock()


def _ua() -> str:
    # Wikimedia asks for a descriptive User-Agent; set WIKI_USER_AGENT to include your contact URL/email.
    import os

    return os.getenv("WIKI_USER_AGENT") or f"{settings.brand_name.replace(' ', '')}ContentBot/1.0 (educational exam-prep content)"


def _cache_path() -> Path:
    p = settings.output_dir / "cache" / "wikimedia.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _cache_get(key: str):
    try:
        return json.loads(_cache_path().read_text(encoding="utf-8")).get(key)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _cache_put(key: str, value) -> None:
    with _lock:
        try:
            data = json.loads(_cache_path().read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
        data[key] = value
        _cache_path().write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _get(url: str, params: dict | None = None) -> httpx.Response | None:
    for attempt in range(4):
        try:
            r = httpx.get(url, params=params, headers={"User-Agent": _ua()}, timeout=30, follow_redirects=True)
        except httpx.HTTPError as e:
            log.warning("wikimedia request failed: %s", e)
            return None
        if r.status_code == 429:
            time.sleep(2 ** attempt * 2)
            continue
        return r
    log.warning("wikimedia rate-limited: %s", url)
    return None


def wiki_summary(title: str, lang: str = "en") -> dict | None:
    key = f"summary:{lang}:{title}"
    cached = _cache_get(key)
    if cached is not None:
        return cached or None
    r = _get(f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(title.replace(' ', '_'))}")
    if r is None:
        return None  # transient: don't cache
    data = r.json() if r.status_code == 200 else {}
    if data.get("type") == "disambiguation":
        data = {}
    keep = {k: data.get(k) for k in ("title", "description", "coordinates", "extract") if data.get(k)}
    if data.get("content_urls"):
        keep["url"] = data["content_urls"]["desktop"]["page"]
    _cache_put(key, keep)
    return keep or None


@dataclass
class Media:
    path: Path
    credit: str
    source_url: str


def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()


def fetch_image(title: str, dest_dir: Path, width: int = 1080) -> Media | None:
    """Lead image of an English Wikipedia article, only if freely licensed on Commons."""
    r = _get("https://en.wikipedia.org/w/api.php", {
        "action": "query", "format": "json", "prop": "pageimages", "piprop": "name", "titles": title, "redirects": 1,
    })
    if r is None or r.status_code != 200:
        return None
    pages = r.json().get("query", {}).get("pages", {})
    fname = next((p.get("pageimage") for p in pages.values() if p.get("pageimage")), None)
    if not fname:
        log.info("no lead image for %r", title)
        return None

    # Query Commons: files that exist only on en.wikipedia (non-free / fair use) won't be found here.
    r = _get("https://commons.wikimedia.org/w/api.php", {
        "action": "query", "format": "json", "titles": f"File:{fname}", "prop": "imageinfo",
        "iiprop": "url|extmetadata", "iiurlwidth": width,
    })
    if r is None or r.status_code != 200:
        return None
    page = next(iter(r.json().get("query", {}).get("pages", {}).values()), {})
    if "imageinfo" not in page:
        log.info("image %s for %r is not on Commons (possibly non-free) - skipped", fname, title)
        return None
    info = page["imageinfo"][0]
    meta = info.get("extmetadata", {})
    lic = _strip_html(meta.get("LicenseShortName", {}).get("value", ""))
    if not FREE_LICENSE.match(lic):
        log.info("image %s licence %r not allowed - skipped", fname, lic)
        return None
    artist = _strip_html(meta.get("Artist", {}).get("value", "")) or "Unknown author"
    url = info.get("thumburl") or info["url"]
    img = _get(url)
    if img is None or img.status_code != 200:
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(urllib.parse.urlparse(url).path).suffix.lower() or ".jpg"
    path = dest_dir / (re.sub(r"[^\w]+", "_", title)[:50] + ext)
    path.write_bytes(img.content)
    credit = f"{title}: photo by {artist[:80]}, {lic}, via Wikimedia Commons"
    return Media(path, credit, info.get("descriptionurl", url))
