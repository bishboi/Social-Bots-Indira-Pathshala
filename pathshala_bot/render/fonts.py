"""Font discovery for every supported script.

Roles:
  regular, medium, semibold, bold, heavy   – sans text (slides, cards, video, notes, labels)
  serif, serif_bold, head, display         – newspaper text, headlines, masthead

Devanagari: Mukta (sans), Martel (serif/head), Rozha One (masthead).
Latin: Poppins (sans), Source Serif 4, Playfair Display, UnifrakturMaguntia.
Other Indian scripts: Noto Sans / Noto Serif <Script>; Urdu: Noto Nastaliq Urdu for newspaper/notes
text and Noto Naskh Arabic on slides (more legible on screen); Kashmiri/Sindhi: Noto Naskh Arabic.
Run scripts/fetch_fonts.sh to (re)download them into assets/fonts/.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from ..config import settings
from ..languages import LANGUAGES, detect_script

log = logging.getLogger(__name__)

SEARCH_DIRS = [
    lambda: settings.fonts_dir,
    lambda: Path("/usr/share/fonts"),
    lambda: Path("/usr/local/share/fonts"),
    lambda: Path.home() / ".fonts",
]

ROLES = ("regular", "medium", "semibold", "bold", "heavy", "serif", "serif_bold", "head", "display")


def _noto(name: str) -> dict[str, list[str]]:
    sans, serif = f"NotoSans{name}", f"NotoSerif{name}"
    return {
        "regular": [f"{sans}-Regular.ttf"], "medium": [f"{sans}-Regular.ttf"],
        "semibold": [f"{sans}-Bold.ttf"], "bold": [f"{sans}-Bold.ttf"], "heavy": [f"{sans}-ExtraBold.ttf", f"{sans}-Bold.ttf"],
        "serif": [f"{serif}-Regular.ttf", f"{sans}-Regular.ttf"], "serif_bold": [f"{serif}-Bold.ttf", f"{sans}-Bold.ttf"],
        "head": [f"{serif}-Bold.ttf", f"{sans}-ExtraBold.ttf"], "display": [f"{serif}-Bold.ttf", f"{sans}-ExtraBold.ttf"],
    }


def _single(family: str) -> dict[str, list[str]]:
    r, b = f"{family}-Regular.ttf", f"{family}-Bold.ttf"
    return {role: ([r] if role in ("regular", "medium", "serif") else [b, r]) for role in ROLES}


FILES: dict[str, dict[str, list[str]]] = {
    "deva": {
        "regular": ["Mukta-Regular.ttf", "NotoSansDevanagari-Regular.ttf"],
        "medium": ["Mukta-Medium.ttf", "Mukta-Regular.ttf", "NotoSansDevanagari-Regular.ttf"],
        "semibold": ["Mukta-SemiBold.ttf", "Mukta-Bold.ttf", "NotoSansDevanagari-Bold.ttf"],
        "bold": ["Mukta-Bold.ttf", "NotoSansDevanagari-Bold.ttf"],
        "heavy": ["Mukta-ExtraBold.ttf", "Mukta-Bold.ttf", "NotoSansDevanagari-Bold.ttf"],
        "serif": ["Martel-Regular.ttf", "NotoSerifDevanagari-Regular.ttf"],
        "serif_bold": ["Martel-Bold.ttf", "NotoSerifDevanagari-Bold.ttf"],
        "head": ["Martel-ExtraBold.ttf", "Martel-Bold.ttf"],
        "display": ["RozhaOne-Regular.ttf", "Martel-ExtraBold.ttf"],
    },
    "latin": {
        "regular": ["Poppins-Regular.ttf", "NotoSans-Regular.ttf", "DejaVuSans.ttf"],
        "medium": ["Poppins-Regular.ttf", "DejaVuSans.ttf"],
        "semibold": ["Poppins-Bold.ttf", "DejaVuSans-Bold.ttf"],
        "bold": ["Poppins-Bold.ttf", "NotoSans-Bold.ttf", "DejaVuSans-Bold.ttf"],
        "heavy": ["Poppins-Bold.ttf", "DejaVuSans-Bold.ttf"],
        "serif": ["SourceSerif4-Regular.ttf", "DejaVuSerif.ttf"],
        "serif_bold": ["SourceSerif4-Bold.ttf", "DejaVuSerif-Bold.ttf"],
        "head": ["PlayfairDisplay-Black.ttf", "SourceSerif4-Bold.ttf"],
        "display": ["UnifrakturMaguntia-Regular.ttf", "PlayfairDisplay-Black.ttf"],
    },
    "beng": _noto("Bengali"), "guru": _noto("Gurmukhi"), "gujr": _noto("Gujarati"), "orya": _noto("Oriya"),
    "taml": _noto("Tamil"), "telu": _noto("Telugu"), "knda": _noto("Kannada"), "mlym": _noto("Malayalam"),
    "arab": _single("NotoNaskhArabic"), "olck": _single("NotoSansOlChiki"), "mtei": _single("NotoSansMeeteiMayek"),
}

# Urdu newspapers and books are set in Nastaliq; use it for reading text, Naskh on slides.
LANGUAGE_OVERRIDES: dict[str, dict[str, list[str]]] = {
    "ur": {"serif": ["NotoNastaliqUrdu-Regular.ttf"], "serif_bold": ["NotoNastaliqUrdu-Bold.ttf"],
           "head": ["NotoNastaliqUrdu-Bold.ttf"], "display": ["NotoNastaliqUrdu-Bold.ttf"]},
}

# Old weight names used by callers before roles existed
_ALIASES = {"regular": "regular", "bold": "bold"}


@lru_cache(maxsize=None)
def _index() -> dict[str, Path]:
    found: dict[str, Path] = {}
    for d in SEARCH_DIRS:
        root = d()
        if root.exists():
            for p in root.rglob("*.[ot]tf"):
                found.setdefault(p.name, p)
    return found


def _first(names: list[str]) -> Path | None:
    idx = _index()
    return next((idx[n] for n in names if n in idx), None)


@lru_cache(maxsize=None)
def font_path(script: str, weight: str = "regular") -> Path:
    """Best font file for a script and role, falling back to Latin (with a warning) if missing."""
    weight = _ALIASES.get(weight, weight)
    found = _first(FILES.get(script, {}).get(weight, []))
    if found:
        return found
    if script != "latin":
        log.warning("No %s font for %s text - run scripts/fetch_fonts.sh", weight, script)
        return font_path("latin", weight)
    found = _first(["DejaVuSans-Bold.ttf" if weight in ("bold", "heavy", "semibold") else "DejaVuSans.ttf"])
    if found:
        return found
    raise RuntimeError("No usable TTF font found; run scripts/fetch_fonts.sh")


def for_language(language: str, role: str = "regular") -> Path:
    """Font for a content language (uses per-language overrides, e.g. Nastaliq for Urdu)."""
    lang = LANGUAGES.get(language)
    script = lang.script if lang else "latin"
    found = _first(LANGUAGE_OVERRIDES.get(language, {}).get(role, []))
    return found or font_path(script, role)


def script_for(text: str, language: str = "en") -> str:
    """The script to render `text` in: the language's script, or whatever script the text is in."""
    lang = LANGUAGES.get(language)
    if lang and lang.script != "latin":
        return lang.script
    return detect_script(text)


def pick(text: str, weight: str = "regular", language: str = "en") -> Path:
    script = script_for(text, language)
    found = _first(LANGUAGE_OVERRIDES.get(language, {}).get(weight, [])) if script == (LANGUAGES[language].script if language in LANGUAGES else None) else None
    return found or font_path(script, weight)


def has_devanagari(text: str) -> bool:  # kept for older callers
    return detect_script(text) == "deva"


def named(filename: str, fallback: tuple[str, str] = ("deva", "regular")) -> Path:
    """A specific bundled font file (e.g. 'Martel-ExtraBold.ttf'), or a sensible fallback."""
    return _index().get(filename) or font_path(*fallback)


def all_files_for(language: str) -> list[Path]:
    """Every font file a language may use (for registering with Pango/Manim)."""
    lang = LANGUAGES.get(language)
    scripts = {"latin", "deva", lang.script if lang else "latin"}
    out = {font_path(s, r) for s in scripts for r in ROLES}
    out |= {for_language(language, r) for r in ROLES}
    return sorted(out)
