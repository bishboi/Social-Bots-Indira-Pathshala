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
MAX_CHARS = 150_000  # a full 100-question paper with its answer key can be long
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


class Doc:
    def __init__(self, url: str, text: str = "", links: list[str] | None = None, pdf: bytes | None = None, pages: int = 0):
        self.url, self.text, self.links, self.pdf, self.pages = url, text, links or [], pdf, pages

    @property
    def scanned(self) -> bool:
        """A PDF of page images (little or no text layer): needs OCR."""
        return self.pdf is not None and self.pages > 0 and len(self.text) < 150 * self.pages


def pdf_pages(data: bytes) -> int:
    exe = shutil.which("pdfinfo")
    if not exe:
        return 0
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(data)
        f.flush()
        try:
            out = subprocess.run([exe, f.name], capture_output=True, timeout=30).stdout.decode("utf-8", "replace")
        except subprocess.TimeoutExpired:
            return 0
    m = re.search(r"Pages:\s+(\d+)", out)
    return int(m.group(1)) if m else 0


def page_images(data: bytes, max_pages: int = 40, dpi: int = 110) -> list[bytes]:
    """PNG of each page (for OCR by a vision model)."""
    exe = shutil.which("pdftoppm")
    if not exe:
        return []
    with tempfile.TemporaryDirectory() as d:
        src = f"{d}/in.pdf"
        with open(src, "wb") as f:
            f.write(data)
        try:
            subprocess.run([exe, "-r", str(dpi), "-png", "-l", str(max_pages), src, f"{d}/p"], capture_output=True, timeout=300)
        except subprocess.TimeoutExpired:
            return []
        import glob

        return [open(p, "rb").read() for p in sorted(glob.glob(f"{d}/p-*.png"))]


def fetch(url: str, client: httpx.Client) -> Doc:
    """Text (and, for PDFs, the file) of a page or PDF, plus the PDF links on a page."""
    from urllib.parse import urljoin

    if not public_url(url):
        return Doc(url)
    try:
        with client.stream("GET", url, headers={"User-Agent": UA}, follow_redirects=True, timeout=40) as r:
            if r.status_code >= 400 or not public_url(str(r.url)):
                return Doc(url)
            chunks, size = [], 0
            for chunk in r.iter_bytes():
                size += len(chunk)
                if size > MAX_BYTES:
                    break
                chunks.append(chunk)
            data, ctype, final = b"".join(chunks), r.headers.get("content-type", "").lower(), str(r.url)
    except httpx.HTTPError as e:
        log.info("could not fetch %s: %s", url, e)
        return Doc(url)
    if data[:4] == b"%PDF" or ("pdf" in ctype and b"%PDF" in data[:1024]):
        return Doc(url, pdf_text(data)[:MAX_CHARS], pdf=data, pages=pdf_pages(data))
    if "pdf" in ctype:
        return Doc(url)
    raw = data.decode(r.encoding or "utf-8", "replace")
    links = []
    for href in re.findall(r"""href=["']([^"'#]+)["']""", raw, re.I):
        full = urljoin(final, html.unescape(href))
        if re.search(r"\.pdf($|\?)", full, re.I):
            links.append(full)
    return Doc(url, html_text(raw)[:MAX_CHARS], list(dict.fromkeys(links)))


def question_score(text: str) -> int:
    """How much a document looks like a solved question paper (options + answers)."""
    opts = len(re.findall(r"(?m)^\s*[\(\[]?(?:[A-Da-d]|[1-4]|[क-घ])[\)\].]\s", text))
    answers = len(re.findall(r"(?i)\b(ans(?:wer)?|उत्तर|सही उत्तर)\b\s*[:\-–)]", text))
    return opts + 3 * answers


def is_answer_key(text: str) -> bool:
    return bool(re.search(r"(?i)answer\s*key|उत्तर\s*कुंजी|model\s+answer", text[:3000])) and \
        len(re.findall(r"(?m)\b\d{1,3}\s+[1-5A-Da-d]\b", text)) >= 25


class Found:
    def __init__(self):
        self.papers: list[tuple[str, str]] = []  # (url, text) of documents with questions + answers
        self.scanned: list[tuple[str, bytes]] = []  # (url, pdf) question papers that need OCR
        self.keys: list[tuple[str, str]] = []  # (url, text) official / model answer keys


def gather(urls: list[str], limit: int = 6, attempts: int = 18, keyword: str = "") -> Found:
    """Documents for a paper: solved papers as text, scanned papers (for OCR) and answer keys.

    Direct PDF links are opened first; PDF links found on hub pages are followed one level deep,
    those whose address matches `keyword` first."""
    def clean(u: str) -> str:
        return u.strip().rstrip(".,);")

    kw = [w for w in re.split(r"[\W_]+", keyword.lower()) if len(w) > 2]

    def match(u: str) -> int:
        return sum(w in u.lower() for w in kw)

    first = [clean(u) for u in urls if re.search(r"\.pdf($|\?)", u, re.I)]
    queue = list(dict.fromkeys(first + [clean(u) for u in urls]))
    seen: set[str] = set()
    papers: list[tuple[int, str, str]] = []
    out = Found()
    tries = 0
    with httpx.Client() as client:
        while queue and tries < attempts:
            url = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            tries += 1
            doc = fetch(url, client)
            score = question_score(doc.text)
            if doc.scanned:
                out.scanned.append((url, doc.pdf))
            elif is_answer_key(doc.text):
                out.keys.append((url, doc.text[:20000]))
            elif len(doc.text) > 800 and score >= 8:
                papers.append((score, url, doc.text))
            if doc.links:
                links = sorted(doc.links, key=lambda u: -match(u))
                queue = [u for u in links[:8] if u not in seen] + queue
    papers.sort(key=lambda x: -x[0])
    out.papers = [(u, t) for _, u, t in papers[:limit]]
    out.scanned.sort(key=lambda x: -match(x[0]))
    log.info("fetched %d document(s): %d with questions, %d scanned, %d answer key(s)", tries, len(out.papers),
             len(out.scanned), len(out.keys))
    return out
