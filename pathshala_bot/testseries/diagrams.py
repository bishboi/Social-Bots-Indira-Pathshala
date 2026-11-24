"""SVG figures for diagram questions, drawn by a model on OpenRouter.

The question writer describes each figure precisely (diagram_brief); an OpenRouter model
(OPENROUTER_MODEL, with OPENROUTER_FALLBACK_MODEL as backup) turns the brief into SVG.
The SVG is sanitised before it is stored: no scripts, event handlers, external links,
entities or embedded HTML, so it is safe to show in the online test and the PDF.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET

import httpx

from ..config import settings

log = logging.getLogger(__name__)

API = "https://openrouter.ai/api/v1/chat/completions"
MAX_BYTES = 80_000

SYSTEM = """You draw clean, exam-paper style figures as SVG.
Rules:
- Reply with ONE complete <svg> element and nothing else (no markdown, no explanation).
- Use xmlns="http://www.w3.org/2000/svg" and a viewBox (about 0 0 480 320; taller or wider only if the figure needs it). No width/height in px.
- Black strokes (#111) on a white background, stroke-width 2; light grey (#ddd) fills only where shading is part of the figure.
- Text: font-family="Arial, sans-serif", font-size 14-18, never overlapping lines or other labels. Keep labels short
  (letters, numbers, units). Write any label exactly as given in the brief.
- Draw exactly what the brief describes - every point, label, value and position - accurately and to scale where it matters.
- NEVER show the answer or anything that gives it away.
- Allowed: path, line, polyline, polygon, rect, circle, ellipse, text, tspan, g, defs, marker, use (href="#id" only).
  No images, scripts, styles with url(), foreignObject, animation or links."""

ALLOWED = {"svg", "g", "defs", "marker", "path", "line", "polyline", "polygon", "rect", "circle", "ellipse", "text",
           "tspan", "use", "title", "desc", "pattern", "clipPath", "linearGradient", "radialGradient", "stop", "symbol"}
NS = "{http://www.w3.org/2000/svg}"
XLINK = "{http://www.w3.org/1999/xlink}href"


def available() -> bool:
    return bool(settings.openrouter_api_key)


def sanitize(svg: str) -> str | None:
    """A safe, standalone SVG string, or None if it isn't usable."""
    m = re.search(r"<svg\b.*</svg>", svg or "", re.S | re.I)
    if not m:
        return None
    text = m.group(0)
    if re.search(r"<!(DOCTYPE|ENTITY)", text, re.I) or len(text.encode()) > MAX_BYTES:
        return None
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    if root.tag not in ("svg", NS + "svg"):
        return None

    def clean(el) -> None:
        for child in list(el):
            tag = child.tag.replace(NS, "") if isinstance(child.tag, str) else ""
            if tag not in ALLOWED:
                el.remove(child)
                continue
            clean(child)
        for k in list(el.attrib):
            v = el.attrib[k]
            name = k.split("}")[-1].lower()
            if name.startswith("on") or (name == "href" and not v.startswith("#")) or "javascript:" in v.lower() \
                    or (name == "style" and "url(" in v.lower() and "url(#" not in v.lower()):
                del el.attrib[k]

    clean(root)
    if not any(True for _ in root.iter() if _ is not root):
        return None
    root.attrib.pop("width", None)
    root.attrib.pop("height", None)
    if "viewBox" not in root.attrib:
        root.set("viewBox", "0 0 480 320")
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
    out = ET.tostring(root, encoding="unicode")
    return out if out.startswith("<svg") else None


def draw(brief: str, question: str, language: str = "en", client: httpx.Client | None = None) -> str | None:
    """SVG for one figure, or None (no key / both models failed / unusable SVG)."""
    if not available() or not brief.strip():
        return None
    models = [m for m in (settings.openrouter_model, settings.openrouter_fallback) if m]
    user = (f"Figure for this exam question (do not reveal its answer):\n{question}\n\nFIGURE BRIEF\n{brief}\n\n"
            f"Labels in the figure: as in the brief (the paper is in language '{language}'; prefer symbols, letters and numbers).")
    http = client or httpx.Client(timeout=180)
    try:
        for attempt in range(2):
            body = {"model": models[0], "models": models, "max_tokens": 6000, "temperature": 0.2,
                    "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]}
            try:
                r = http.post(API, json=body, headers={"Authorization": f"Bearer {settings.openrouter_api_key}",
                                                       "X-Title": settings.brand_name})
                r.raise_for_status()
                content = r.json()["choices"][0]["message"]["content"] or ""
                if isinstance(content, list):  # some providers return content parts
                    content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
            except Exception as e:  # noqa: BLE001 - network, quota, provider errors
                log.warning("OpenRouter diagram failed (try %d): %s", attempt + 1, str(e)[:200])
                continue
            svg = sanitize(content)
            if svg:
                return svg
            log.warning("OpenRouter returned no usable SVG (try %d)", attempt + 1)
        return None
    finally:
        if client is None:
            http.close()
