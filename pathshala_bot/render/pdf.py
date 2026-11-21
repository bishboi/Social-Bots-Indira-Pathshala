"""Printable revision notes: Jinja HTML -> PDF via WeasyPrint (HarfBuzz shaping handles Devanagari)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import PACKAGE_DIR, Exam, settings
from ..models import ContentPackage
from . import fonts

LABELS = {
    "hi": dict(key_points="मुख्य बिंदु", mnemonic="याद रखने की ट्रिक", cheatsheet="चीट शीट", term="विषय", detail="तथ्य",
               practice="अभ्यास प्रश्न", answers="उत्तर कुंजी व व्याख्या", sources="स्रोत", map="मानचित्र", cue="संकेत", stands_for="किसके लिए", tip="कैसे काम आएगी",
               rights="सर्वाधिकार सुरक्षित। इस PDF को बेचना, बदलना या बिना अनुमति के दोबारा प्रकाशित करना वर्जित है। निःशुल्क व्यक्तिगत अध्ययन हेतु साझा कर सकते हैं।"),
    "en": dict(key_points="Key Points", mnemonic="Memory Trick", cheatsheet="Cheatsheet", term="Term", detail="Fact",
               practice="Practice Questions", answers="Answer Key & Explanations", sources="Sources", map="Map", cue="Cue", stands_for="Stands for", tip="How to use it",
               rights="All rights reserved. Selling, altering or republishing this PDF without permission is prohibited. You may share it free of charge for personal study."),
}

_env = Environment(loader=FileSystemLoader(PACKAGE_DIR / "templates"), autoescape=select_autoescape(["html"]))


def render_pdf(pkg: ContentPackage, exam: Exam, out: Path, map_png: Path | None = None) -> Path:
    from weasyprint import HTML  # imported lazily: needs system Pango

    script = "deva" if exam.language == "hi" or fonts.has_devanagari(pkg.title + pkg.topic) else "latin"
    faces = [
        {"family": "PSans", "url": fonts.font_path(script, "regular").as_uri(), "weight": 400},
        {"family": "PSans", "url": fonts.font_path(script, "bold").as_uri(), "weight": 700},
    ]
    html = _env.get_template("notes.html").render(
        pkg=pkg, exam=exam, brand=settings.brand_name, handle=settings.brand_handle,
        lang=exam.language if exam.language != "hinglish" else "en",
        t=LABELS["hi" if exam.language == "hi" else "en"], font_faces=faces,
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
