"""Printable revision notes: Jinja HTML -> PDF via WeasyPrint (HarfBuzz shaping handles Devanagari)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import PACKAGE_DIR, Exam, settings
from ..models import ContentPackage
from . import fonts

_env = Environment(loader=FileSystemLoader(PACKAGE_DIR / "templates"), autoescape=select_autoescape(["html"]))


def render_pdf(pkg: ContentPackage, exam: Exam, out: Path, map_png: Path | None = None) -> Path:
    from weasyprint import HTML  # imported lazily: needs system Pango

    from .. import i18n
    from ..languages import get as get_language

    lang = get_language(exam.language)
    # Notes are set in the language's sans face (Urdu: Nastaliq, the usual reading style); Devanagari and Latin
    # faces are added as fallbacks for mixed text such as English terms or Hindi place names.
    body = "serif" if lang.code == "ur" else "regular"
    faces = [
        {"family": "PSans", "url": fonts.for_language(lang.code, body).as_uri(), "weight": 400},
        {"family": "PSans", "url": fonts.for_language(lang.code, "serif_bold" if lang.code == "ur" else "bold").as_uri(), "weight": 700},
        {"family": "PDeva", "url": fonts.font_path("deva", "regular").as_uri(), "weight": 400},
        {"family": "PLatin", "url": fonts.font_path("latin", "regular").as_uri(), "weight": 400},
    ]
    html = _env.get_template("notes.html").render(
        pkg=pkg, exam=exam, brand=settings.brand_name, handle=settings.brand_handle,
        lang=lang.code if lang.code != "hinglish" else "en", rtl=lang.rtl,
        t=i18n.labels(lang.code), font_faces=faces,
        map_url=map_png.resolve().as_uri() if map_png else None,
        logo_url=settings.brand_logo.resolve().as_uri() if settings.brand_logo.exists() else None,
        watermark=settings.pdf_watermark,
        year=date.today().year,
        owner=settings.copyright_owner or settings.brand_name,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    (out.with_suffix(".html")).write_text(html, encoding="utf-8")
    HTML(string=html, base_url=str(out.parent)).write_pdf(str(out))
    return out
