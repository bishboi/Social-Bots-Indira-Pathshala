"""Question paper and solutions PDFs (WeasyPrint), in the exam's language, with the brand's logo and watermark."""

from __future__ import annotations

import base64
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import PACKAGE_DIR, Exam, settings
from .models import KINDS, Blueprint, Test

_env = Environment(loader=FileSystemLoader(PACKAGE_DIR / "templates"), autoescape=select_autoescape(["html"]))
LETTERS = "ABCDEFGHIJ"


def svg_uri(svg: str) -> str:
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode()


def context(test: Test, exam: Exam, bp: Blueprint | None) -> dict:
    from .. import i18n
    from ..languages import get as get_language
    from ..render import fonts

    lang = get_language(test.language)
    body = "serif" if lang.code == "ur" else "regular"
    faces = [
        {"family": "PSans", "url": fonts.for_language(lang.code, body).as_uri(), "weight": 400},
        {"family": "PSans", "url": fonts.for_language(lang.code, "serif_bold" if lang.code == "ur" else "bold").as_uri(), "weight": 700},
        {"family": "PDeva", "url": fonts.font_path("deva", "regular").as_uri(), "weight": 400},
        {"family": "PLatin", "url": fonts.font_path("latin", "regular").as_uri(), "weight": 400},
    ]
    numbered, n = [], 0
    for sec in test.sections:
        items = []
        qs = test.by_id()
        for qid in sec.question_ids:
            if qid in qs:
                n += 1
                items.append((n, qs[qid]))
        numbered.append((sec, items))
    return dict(
        test=test, exam=exam, bp=bp, kind=KINDS[test.kind], sections=numbered, letters=LETTERS, svg_uri=svg_uri,
        brand=settings.brand_name, handle=settings.brand_handle, lang=lang.code if lang.code != "hinglish" else "en",
        rtl=lang.rtl, font_faces=faces, t=i18n.labels(lang.code),
        logo_url=settings.brand_logo.resolve().as_uri() if settings.brand_logo.exists() else None,
        watermark=settings.pdf_watermark, year=date.today().year, owner=settings.copyright_owner or settings.brand_name,
        instructions=(bp.instructions if bp else []), total=n,
        practice_label=(f"अभ्यास प्रश्न - {test.year} के मूल पेपर से नहीं" if test.language == "hi"
                        else f"Practice question - not from the actual {test.year} paper"),
    )


def render_pdfs(test: Test, exam: Exam, bp: Blueprint | None, out_dir: Path) -> dict[str, Path]:
    from weasyprint import HTML  # needs system Pango

    ctx = context(test, exam, bp)
    out = {}
    for mode in ("paper", "solutions"):
        html = _env.get_template("test_paper.html").render(mode=mode, **ctx)
        path = out_dir / f"{mode}.pdf"
        HTML(string=html, base_url=str(out_dir)).write_pdf(str(path))
        out[mode] = path
    return out
