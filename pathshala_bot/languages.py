"""All content languages: the 22 languages of the Eighth Schedule, plus English and Hinglish.

Each language has a script (which fonts to use), a writing direction, the instruction given
to the AI, and the voices that can read it. Codes are ISO 639 (2-letter where one exists).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Script:
    id: str
    name: str
    ranges: tuple[tuple[int, int], ...]  # Unicode blocks, for detecting the script of a text
    rtl: bool = False


SCRIPTS: dict[str, Script] = {s.id: s for s in [
    Script("latin", "Latin", ((0x0041, 0x024F),)),
    Script("deva", "Devanagari", ((0x0900, 0x097F), (0xA8E0, 0xA8FF))),
    Script("beng", "Bengali-Assamese", ((0x0980, 0x09FF),)),
    Script("guru", "Gurmukhi", ((0x0A00, 0x0A7F),)),
    Script("gujr", "Gujarati", ((0x0A80, 0x0AFF),)),
    Script("orya", "Odia", ((0x0B00, 0x0B7F),)),
    Script("taml", "Tamil", ((0x0B80, 0x0BFF),)),
    Script("telu", "Telugu", ((0x0C00, 0x0C7F),)),
    Script("knda", "Kannada", ((0x0C80, 0x0CFF),)),
    Script("mlym", "Malayalam", ((0x0D00, 0x0D7F),)),
    Script("arab", "Perso-Arabic", ((0x0600, 0x06FF), (0x0750, 0x077F), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF)), rtl=True),
    Script("olck", "Ol Chiki", ((0x1C50, 0x1C7F),)),
    Script("mtei", "Meitei Mayek", ((0xABC0, 0xABFF), (0xAAE0, 0xAAFF))),
]}


@dataclass(frozen=True)
class Language:
    code: str
    name: str          # English name
    native: str        # name in the language itself
    script: str        # SCRIPTS key
    edge_voice: str = ""          # Microsoft Edge neural voice (free)
    eleven_model: str = ""        # ElevenLabs model that supports it ("" = none)
    voice_fallback: str = ""      # another language's voice that can read this script approximately
    gemini: str = "lite"          # Gemini TTS: "lite" (gemini-3.8-flash-lite-tts), "flash" (gemini-3.8-flash-tts) or "" (not listed)
    style: str = ""               # extra writing guidance for the AI
    aliases: tuple[str, ...] = field(default=())

    @property
    def rtl(self) -> bool:
        return SCRIPTS[self.script].rtl

    def ai_rule(self) -> str:
        if self.code == "en":
            return "Write all learner-facing text in clear Indian English."
        if self.code == "hinglish":
            return ("Write in simple Hinglish (Hindi in Roman script mixed with English terms), the way popular Indian "
                    "educators talk. Keep formulas and technical terms in English.")
        rule = (f"Write ALL learner-facing text in {self.name} ({self.native}) using the {SCRIPTS[self.script].name} script. "
                "Use the standard vocabulary of competitive-exam and textbook material in this language; keep formulas, "
                "units, section numbers and widely used abbreviations as they are normally written in this language's study material.")
        return f"{rule} {self.style}".strip()


_V3 = "eleven_v3"
_V2 = "eleven_multilingual_v2"

LANGUAGES: dict[str, Language] = {l.code: l for l in [
    Language("en", "English", "English", "latin", "en-IN-NeerjaNeural", _V2),
    Language("hinglish", "Hinglish", "Hinglish", "latin", "hi-IN-SwaraNeural", _V2),
    Language("hi", "Hindi", "हिन्दी", "deva", "hi-IN-SwaraNeural", _V2,
             style="Keep standard technical terms and proper nouns as commonly written in Hindi-medium exam material."),
    Language("as", "Assamese", "অসমীয়া", "beng", "", _V3, voice_fallback="bn"),
    Language("bn", "Bengali", "বাংলা", "beng", "bn-IN-TanishaaNeural", _V3),
    Language("brx", "Bodo", "बड़ो", "deva", voice_fallback="hi"),
    Language("doi", "Dogri", "डोगरी", "deva", voice_fallback="hi"),
    Language("gu", "Gujarati", "ગુજરાતી", "gujr", "gu-IN-DhwaniNeural", _V3),
    Language("kn", "Kannada", "ಕನ್ನಡ", "knda", "kn-IN-SapnaNeural", _V3),
    Language("ks", "Kashmiri", "کٲشُر", "arab", voice_fallback="ur",
             style="Use the Perso-Arabic script as officially used for Kashmiri."),
    Language("kok", "Konkani", "कोंकणी", "deva", voice_fallback="mr",
             style="Use Devanagari, the official script of Konkani in Goa."),
    Language("mai", "Maithili", "मैथिली", "deva", voice_fallback="hi"),
    Language("ml", "Malayalam", "മലയാളം", "mlym", "ml-IN-SobhanaNeural", _V3),
    Language("mni", "Manipuri", "ꯃꯤꯇꯩꯂꯣꯟ", "mtei",
             style="Use the Meitei Mayek script, the official script of Manipuri."),
    Language("mr", "Marathi", "मराठी", "deva", "mr-IN-AarohiNeural", _V3),
    Language("ne", "Nepali", "नेपाली", "deva", "ne-NP-HemkalaNeural", _V3),
    Language("or", "Odia", "ଓଡ଼ିଆ", "orya", "", _V3, aliases=("od", "oriya")),
    Language("pa", "Punjabi", "ਪੰਜਾਬੀ", "guru", "", _V3, style="Use the Gurmukhi script."),
    Language("sa", "Sanskrit", "संस्कृतम्", "deva", voice_fallback="hi"),
    Language("sat", "Santali", "ᱥᱟᱱᱛᱟᱲᱤ", "olck", style="Use the Ol Chiki script, the official script of Santali."),
    Language("sd", "Sindhi", "سنڌي", "arab", voice_fallback="ur", style="Use the Perso-Arabic script for Sindhi."),
    Language("ta", "Tamil", "தமிழ்", "taml", "ta-IN-PallaviNeural", _V2),
    Language("te", "Telugu", "తెలుగు", "telu", "te-IN-ShrutiNeural", _V3),
    Language("ur", "Urdu", "اردو", "arab", "ur-IN-GulNeural", _V3),
]}

# Gemini TTS coverage per Google's language tables for gemini-3.8-flash-lite-tts / gemini-3.8-flash-tts:
# not listed for either -> "" (these use Edge or a related language's voice instead)
_GEMINI_OVERRIDES = {"ur": "", "sa": "", "kok": "", "doi": "", "brx": "", "sd": "flash"}
LANGUAGES = {c: (Language(**{**l.__dict__, "gemini": _GEMINI_OVERRIDES[c]}) if c in _GEMINI_OVERRIDES else l)
             for c, l in LANGUAGES.items()}

# Back-compat / convenience spellings
_ALIASES = {a: l.code for l in LANGUAGES.values() for a in l.aliases}


def get(code: str | None) -> Language:
    code = (code or "en").strip().lower()
    code = _ALIASES.get(code, code)
    if code not in LANGUAGES:
        raise KeyError(f"Unknown language '{code}'. Known: {', '.join(LANGUAGES)}")
    return LANGUAGES[code]


def is_known(code: str | None) -> bool:
    try:
        get(code)
        return True
    except KeyError:
        return False


def detect_script(text: str) -> str:
    """Script of the first non-Latin letter in `text` (else 'latin')."""
    for ch in text or "":
        cp = ord(ch)
        if cp < 0x0370:
            continue
        for s in SCRIPTS.values():
            if s.id != "latin" and any(a <= cp <= b for a, b in s.ranges):
                return s.id
    return "latin"


def choices() -> dict[str, str]:
    """code -> 'Native (English)' for dropdowns, English/Hinglish/Hindi first."""
    order = ["hi", "en", "hinglish"] + sorted((c for c in LANGUAGES if c not in ("hi", "en", "hinglish")), key=lambda c: LANGUAGES[c].name)
    return {c: (LANGUAGES[c].native if LANGUAGES[c].native == LANGUAGES[c].name else f"{LANGUAGES[c].native} ({LANGUAGES[c].name})") for c in order}
