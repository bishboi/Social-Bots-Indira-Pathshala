"""Read the pages and PDFs that research found, so previous-year questions come from the paper itself.

Web search returns snippets; a question paper needs the full page. URLs come from search
results, so only public http(s) hosts are fetched (no internal addresses), with size and
time limits. PDFs are read with `pdftotext` (poppler-utils) when it is installed.
"""

from __future__ import annotations

import html
import ipaddress
import logging
import re
import shutil
import socket
import subprocess
import tempfile
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)
MAX_BYTES = 15_000_000
MAX_CHARS = 60_000
UA = "Mozilla/5.0 (compatible; exam-prep-research/1.0)"


def public_url(url: str) -> bool:
    try:
        u = urlparse(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            return False
        for info in socket.getaddrinfo(u.hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
        return True
    except (OSError, ValueError):
        return False


def html_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h\d|table|ol|ul)>", "\n", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    text = html.unescape(raw)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def pdf_text(data: bytes) -> str:
    exe = shutil.which("pdftotext")
    if not exe:
        return ""
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(data)
        f.flush()
        try:
            out = subprocess.run([exe, "-layout", "-l", "60", f.name, "-"], capture_output=True, timeout=60)
        except subprocess.TimeoutExpired:
            return ""
    return out.stdout.decode("utf-8", "replace").strip()


def fetch(url: str, client: httpx.Client) -> str:
    """Readable text of a page or PDF ('' when it can't be read)."""
    if not public_url(url):
        return ""
    try:
        with client.stream("GET", url, headers={"User-Agent": UA}, follow_redirects=True, timeout=40) as r:
            if r.status_code >= 400 or not public_url(str(r.url)):
                return ""
            chunks, size = [], 0
            for chunk in r.iter_bytes():
                size += len(chunk)
                if size > MAX_BYTES:
                    break
                chunks.append(chunk)
            data, ctype = b"".join(chunks), r.headers.get("content-type", "").lower()
    except httpx.HTTPError as e:
        log.info("could not fetch %s: %s", url, e)
        return ""
    if "pdf" in ctype or data[:4] == b"%PDF":
        text = pdf_text(data)
    else:
        text = html_text(data.decode(r.encoding or "utf-8", "replace"))
    return text[:MAX_CHARS]


def gather(urls: list[str], limit: int = 6) -> list[tuple[str, str]]:
    """(url, text) for the first `limit` readable URLs."""
    out = []
    seen = set()
    with httpx.Client() as client:
        for url in urls:
            url = url.strip().rstrip(".,);")
            if url in seen:
                continue
            seen.add(url)
            text = fetch(url, client)
            if len(text) > 800:
                out.append((url, text))
            if len(out) >= limit:
                break
    return out
