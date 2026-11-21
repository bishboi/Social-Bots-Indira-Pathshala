"""Font discovery.

Mukta (Devanagari + Latin sans, 5 weights) for Hindi/Hinglish text on slides, cards, notes and video;
Poppins for English-only text. Noto fonts remain as fallbacks. Weights: regular, medium, semibold, bold, heavy.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

from ..config import settings

log = logging.getLogger(__name__)

DEVANAGARI = re.compile(r"[ऀ-ॿ]")

SEARCH_DIRS = [
    lambda: settings.fonts_dir,
    lambda: Path("/usr/share/fonts"),
    lambda: Path("/usr/local/share/fonts"),
    lambda: Path.home() / ".fonts",
]

CANDIDATES = {
    ("deva", "regular"): ["Mukta-Regular.ttf", "NotoSansDevanagari-Regular.ttf", "Lohit-Devanagari.ttf"],
    ("deva", "medium"): ["Mukta-Medium.ttf", "Mukta-Regular.ttf", "NotoSansDevanagari-Regular.ttf"],
    ("deva", "semibold"): ["Mukta-SemiBold.ttf", "Mukta-Bold.ttf", "NotoSansDevanagari-Bold.ttf"],
    ("deva", "bold"): ["Mukta-Bold.ttf", "NotoSansDevanagari-Bold.ttf", "Lohit-Devanagari.ttf"],
    ("deva", "heavy"): ["Mukta-ExtraBold.ttf", "Mukta-Bold.ttf", "NotoSansDevanagari-Bold.ttf"],
    ("latin", "regular"): ["Poppins-Regular.ttf", "NotoSans-Regular.ttf", "DejaVuSans.ttf"],
    ("latin", "medium"): ["Poppins-Regular.ttf", "NotoSans-Regular.ttf", "DejaVuSans.ttf"],
    ("latin", "semibold"): ["Poppins-Bold.ttf", "NotoSans-Bold.ttf", "DejaVuSans-Bold.ttf"],
    ("latin", "bold"): ["Poppins-Bold.ttf", "NotoSans-Bold.ttf", "DejaVuSans-Bold.ttf"],
    ("latin", "heavy"): ["Poppins-Bold.ttf", "NotoSans-Bold.ttf", "DejaVuSans-Bold.ttf"],
}


def has_devanagari(text: str) -> bool:
    return bool(DEVANAGARI.search(text or ""))


@lru_cache(maxsize=None)
def _index() -> dict[str, Path]:
    found: dict[str, Path] = {}
    for d in SEARCH_DIRS:
        root = d()
        if root.exists():
            for p in root.rglob("*.[ot]tf"):
                found.setdefault(p.name, p)
    return found


@lru_cache(maxsize=None)
def font_path(script: str, weight: str) -> Path:
    idx = _index()
    for name in CANDIDATES[(script, weight)]:
        if name in idx:
            return idx[name]
    if script == "deva":
        log.warning("No Devanagari font found - run scripts/fetch_fonts.sh. Hindi text will not render.")
        return font_path("latin", weight)
    for name in CANDIDATES[("latin", weight)] + ["DejaVuSans.ttf"]:
        if name in idx:
            return idx[name]
    raise RuntimeError("No usable TTF font found; run scripts/fetch_fonts.sh")


def pick(text: str, weight: str = "regular", language: str = "en") -> Path:
    script = "deva" if (language == "hi" or has_devanagari(text)) else "latin"
    return font_path(script, weight)


def named(filename: str, fallback: tuple[str, str] = ("deva", "regular")) -> Path:
    """A specific bundled font file (e.g. 'Martel-ExtraBold.ttf'), or a sensible fallback."""
    return _index().get(filename) or font_path(*fallback)
