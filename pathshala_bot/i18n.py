"""On-page labels in every content language.

English and Hindi are built in. For other languages the AI translates the label set once;
the result is cached in <OUTPUT_DIR>/cache/i18n/<code>.json (edit it to correct wording).
A hand-made file in assets/i18n/<code>.json takes priority over the cache.
Dates (weekday, month) come from CLDR via Babel, never from the AI.
Without an AI key (or if translation fails) missing labels fall back to English.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import date
from functools import lru_cache
from pathlib import Path

from .config import PROJECT_DIR, settings
from .languages import LANGUAGES, get as get_language

log = logging.getLogger(__name__)

EN: dict[str, str] = {
    # PDF notes
    "key_points": "Key Points", "mnemonic": "Memory Trick", "cheatsheet": "Cheatsheet", "term": "Term", "detail": "Fact",
    "practice": "Practice Questions", "answers": "Answer Key & Explanations", "sources": "Sources", "map": "Map",
    "cue": "Cue", "stands_for": "Stands for", "tip": "How to use it",
    "rights": "All rights reserved. Selling, altering or republishing this PDF without permission is prohibited. You may share it free of charge for personal study.",
    # slide / video scene labels
    "k_title": "DID YOU KNOW?", "k_bullets": "KEY POINTS", "k_fact": "FACT", "k_quiz": "QUIZ TIME", "k_reveal": "ANSWER",
    "k_timeline": "TIMELINE", "k_map": "ON THE MAP", "k_formula": "FORMULA", "k_comparison": "COMPARE", "k_image": "LOOK",
    "k_cta": "FOLLOW", "join_telegram": "Join Telegram",
    "k_pyq": "PREVIOUS YEAR QUESTION", "answer": "Answer", "solution": "Solution",
    "pyq_opener": "Friends, this question was asked in the {exam} exam in {year}...",
    # Telegram
    "memory_trick": "Memory trick", "pdf_caption": "Free PDF notes by {brand}", "top_stories": "Today's top stories",
    # newspaper
    "akhbar_title": "Indira Pathshala Samsamayiki Patrika", "akhbar_title_top": "Indira Pathshala",
    "akhbar_title_main": "Samsamayiki Patrika", "edition": "{region} edition", "national_edition": "National edition",
    "strip_central": "Daily current affairs for central government exams",
    "strip_state": "Daily current affairs for all state government exams",
    "key_facts": "Key facts", "exam_angle": "Exam angle", "static_gk": "Static GK", "source": "Source", "photo": "Photo",
    "desk": "Pathshala Desk", "inside": "Inside", "in_brief": "In brief", "did_you_know": "Did you know?",
    "quiz": "Quiz", "todays_quiz": "Today's Quiz", "at_a_glance": "At a glance", "in_one_line": "In one line",
    "answers_short": "Answers", "vol": "Vol.", "issue": "Issue", "price_free": "Price: Free", "page": "Page",
    "ear_morning": "Every morning on Telegram", "ear_read": "Read · Share", "motto": "Education | Respect | Opportunity",
    "akhbar_rights": "All rights reserved. Free to share for personal study; selling or republishing without permission is prohibited. News summaries are based on the sources listed with each story; photos from Wikimedia Commons under their stated licences.",
    # news categories
    "cat_national": "National", "cat_state": "State", "cat_international": "International", "cat_economy": "Economy",
    "cat_polity": "Polity & Governance", "cat_schemes": "Schemes", "cat_science_tech": "Science & Tech",
    "cat_environment": "Environment", "cat_defence": "Defence", "cat_sports": "Sports",
    "cat_awards_appointments": "Awards & Appointments", "cat_exam_notice": "Exam Notice", "cat_other": "Miscellaneous",
    # newspaper sections
    "sec_state": "State", "sec_nation": "Nation", "sec_exam": "Exams & Jobs", "sec_economy": "Economy", "sec_world": "World",
    "sec_science": "Science & Environment", "sec_people": "Awards & Appointments", "sec_sports": "Sports", "sec_misc": "Miscellany",
}

HI: dict[str, str] = {
    "key_points": "मुख्य बिंदु", "mnemonic": "याद रखने की ट्रिक", "cheatsheet": "चीट शीट", "term": "विषय", "detail": "तथ्य",
    "practice": "अभ्यास प्रश्न", "answers": "उत्तर कुंजी व व्याख्या", "sources": "स्रोत", "map": "मानचित्र",
    "cue": "संकेत", "stands_for": "किसके लिए", "tip": "कैसे काम आएगी",
    "rights": "सर्वाधिकार सुरक्षित। इस PDF को बेचना, बदलना या बिना अनुमति के दोबारा प्रकाशित करना वर्जित है। निःशुल्क व्यक्तिगत अध्ययन हेतु साझा कर सकते हैं।",
    "k_title": "क्या आप जानते हैं?", "k_bullets": "मुख्य बिंदु", "k_fact": "रोचक तथ्य", "k_quiz": "क्विज़ टाइम", "k_reveal": "सही उत्तर",
    "k_timeline": "समयरेखा", "k_map": "मानचित्र पर", "k_formula": "सूत्र", "k_comparison": "तुलना", "k_image": "देखिए",
    "k_cta": "फॉलो करें", "join_telegram": "Telegram जॉइन करें",
    "k_pyq": "पिछले वर्ष का प्रश्न", "answer": "उत्तर", "solution": "हल",
    "pyq_opener": "दोस्तों, यह प्रश्न साल {year} में {exam} परीक्षा में पूछा गया था...",
    "memory_trick": "याद रखने की ट्रिक", "pdf_caption": "{brand} के निःशुल्क PDF नोट्स", "top_stories": "आज के मुख्य समाचार",
    "akhbar_title": "इंदिरा पाठशाला समसामयिकी पत्रिका", "akhbar_title_top": "इंदिरा पाठशाला",
    "akhbar_title_main": "समसामयिकी पत्रिका", "edition": "{region} संस्करण", "national_edition": "राष्ट्रीय संस्करण",
    "strip_central": "केंद्रीय प्रतियोगी परीक्षाओं हेतु दैनिक करंट अफेयर्स",
    "strip_state": "राज्य की सभी प्रतियोगी परीक्षाओं हेतु दैनिक करंट अफेयर्स",
    "key_facts": "मुख्य तथ्य", "exam_angle": "परीक्षा दृष्टि", "static_gk": "स्टैटिक जीके", "source": "स्रोत", "photo": "फ़ोटो",
    "desk": "पाठशाला डेस्क", "inside": "इस अंक में", "in_brief": "संक्षेप में", "did_you_know": "क्या आप जानते हैं?",
    "quiz": "प्रश्नोत्तरी", "todays_quiz": "आज की प्रश्नोत्तरी", "at_a_glance": "एक नज़र में", "in_one_line": "एक पंक्ति में",
    "answers_short": "उत्तर", "vol": "वर्ष", "issue": "अंक", "price_free": "मूल्य: निःशुल्क", "page": "पृष्ठ",
    "ear_morning": "रोज़ सुबह टेलीग्राम पर", "ear_read": "पढ़ें · साझा करें", "motto": "शिक्षा | सम्मान | अवसर",
    "akhbar_rights": "सर्वाधिकार सुरक्षित। निःशुल्क व्यक्तिगत अध्ययन हेतु साझा कर सकते हैं; बेचना या बिना अनुमति पुनः प्रकाशित करना वर्जित है। समाचार सारांश प्रत्येक ख़बर के साथ दिए गए स्रोतों पर आधारित हैं; फ़ोटो विकिमीडिया कॉमन्स से, लाइसेंस सहित।",
    "cat_national": "राष्ट्रीय", "cat_state": "राज्य", "cat_international": "अंतरराष्ट्रीय", "cat_economy": "अर्थव्यवस्था",
    "cat_polity": "राजव्यवस्था", "cat_schemes": "योजनाएँ", "cat_science_tech": "विज्ञान-प्रौद्योगिकी", "cat_environment": "पर्यावरण",
    "cat_defence": "रक्षा", "cat_sports": "खेल", "cat_awards_appointments": "पुरस्कार-नियुक्तियाँ", "cat_exam_notice": "परीक्षा सूचना",
    "cat_other": "विविध",
    "sec_state": "राज्य", "sec_nation": "देश", "sec_exam": "परीक्षा व भर्ती", "sec_economy": "अर्थव्यवस्था", "sec_world": "विदेश",
    "sec_science": "विज्ञान व पर्यावरण", "sec_people": "पुरस्कार व नियुक्तियाँ", "sec_sports": "खेल", "sec_misc": "विविध",
}

_lock = threading.Lock()
_failed: dict[str, float] = {}  # language -> time of the last failed translation (retry after RETRY_AFTER)
RETRY_AFTER = 600


def _region_keys() -> dict[str, str]:
    from .akhbar import STATE_HI

    return {region_key(r): r for r in STATE_HI}


def region_key(region: str) -> str:
    return "region_" + re.sub(r"[^a-z0-9]+", "_", region.lower()).strip("_")


def _english_full() -> dict[str, str]:
    return {**EN, **_region_keys()}


def _hindi_full() -> dict[str, str]:
    from .akhbar import STATE_HI

    return {**HI, **{region_key(r): hi for r, hi in STATE_HI.items()}}


def _cache_file(code: str) -> Path:
    return settings.output_dir / "cache" / "i18n" / f"{code}.json"


def _bundled_file(code: str) -> Path:
    return PROJECT_DIR / "assets" / "i18n" / f"{code}.json"


def _read(path: Path) -> dict[str, str]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def translate_pack(code: str) -> dict[str, str]:
    """Ask the AI to translate every English label into `code` (one structured call)."""
    from pydantic import BaseModel, create_model

    from . import llm

    lang = get_language(code)
    source = _english_full()
    model: type[BaseModel] = create_model(f"Labels_{code}", **{k: (str, ...) for k in source})
    system = (f"You translate short user-interface labels for an Indian exam-preparation brand into {lang.name} "
              f"({lang.native}), written in the {lang.script} script as specified. {lang.ai_rule()}")
    user = ("Translate each value. Rules: keep {brand}, {region}, {exam} and {year} placeholders exactly; pyq_opener is spoken "
            "warmly by a teacher to students at the start of a video (like 'Dosto, ...' in Hindi); keep 'Telegram', 'PDF', 'Quiz' "
            "brand/technical words if that is how they are normally written; keep labels short (newspaper/app style); "
            "keys starting with region_ are names of Indian states/UTs - give their standard name in this language; "
            "akhbar_title is the newspaper's name: render 'Indira Pathshala' as a proper name plus a natural translation of "
            "'Samsamayiki Patrika' (current-affairs magazine); akhbar_title_top must be exactly the 'Indira Pathshala' part of "
            "your akhbar_title and akhbar_title_main exactly the rest.\n\n" + json.dumps(source, ensure_ascii=False, indent=1))
    out = llm.structured(system, user, model, max_tokens=16000, effort="low")
    pack = {k: str(v).strip() for k, v in out.model_dump().items() if str(v).strip()}
    # keep placeholders intact
    for k, v in list(pack.items()):
        for ph in ("{brand}", "{region}", "{exam}", "{year}"):
            if ph in source[k] and ph not in v:
                pack[k] = source[k]
    return pack


@lru_cache(maxsize=64)
def _cached_pack(code: str, stamp: float) -> dict[str, str]:  # stamp = cache file mtime (invalidates edits)
    return {**_read(_cache_file(code)), **_read(_bundled_file(code))}


def labels(code: str | None, translate: bool = True) -> dict[str, str]:
    """All labels for a language (missing ones fall back to English)."""
    code = get_language(code).code if code else "en"
    base = _english_full()
    if code in ("en", "hinglish"):
        return base
    if code == "hi":
        return {**base, **_hindi_full(), **_read(_bundled_file("hi"))}
    cache = _cache_file(code)
    pack = _cached_pack(code, cache.stat().st_mtime if cache.exists() else 0.0)
    import time

    # translate when labels are missing (first use, or new labels added in an update)
    if translate and set(base) - set(pack) and time.time() - _failed.get(code, 0) > RETRY_AFTER:
        with _lock:
            pack = _cached_pack(code, cache.stat().st_mtime if cache.exists() else 0.0)
            if set(base) - set(pack):
                try:
                    fresh = translate_pack(code)
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    cache.write_text(json.dumps(fresh, ensure_ascii=False, indent=1), encoding="utf-8")
                    pack = {**fresh, **_read(_bundled_file(code))}
                    log.info("translated %d labels into %s (cached at %s)", len(fresh), code, cache)
                except Exception as e:  # no key / API error: English for now, retried after RETRY_AFTER
                    _failed[code] = time.time()
                    log.warning("could not translate labels into %s (%s) - using English for now", code, e)
    return {**base, **pack}


def t(code: str | None, key: str, **fmt) -> str:
    text = labels(code).get(key, EN.get(key, key))
    return text.format(**fmt) if fmt else text


HINGLISH_PYQ_OPENER = "Dosto, yeh prashan saal {year} me {exam} exam me poocha gaya tha..."


def pyq_opener(code: str | None, exam: str, year: str) -> str:
    """Opening line of a previous-year-question short, e.g. 'Dosto, yeh prashan saal 2021 me RPSC SI exam me poocha gaya tha...'."""
    if (code or "").lower() == "hinglish":
        return HINGLISH_PYQ_OPENER.format(exam=exam, year=year)
    text = labels(code).get("pyq_opener") or EN["pyq_opener"]
    try:
        return text.format(exam=exam, year=year)
    except (KeyError, IndexError):  # a hand-edited label with broken placeholders
        return EN["pyq_opener"].format(exam=exam, year=year)


def region_name(code: str | None, region: str) -> str:
    return labels(code).get(region_key(region), region)


def format_date(day: date, code: str | None) -> str:
    """Weekday, day month year in the language (CLDR via Babel); English if unavailable."""
    code = get_language(code).code if code else "en"
    loc = {"hinglish": "en_IN", "en": "en_IN", "mni": "mni_Mtei"}.get(code, code)
    try:
        from babel.dates import format_date as babel_format

        return babel_format(day, "EEEE, d MMMM y", locale=loc)
    except Exception:
        try:
            from babel.dates import format_date as babel_format

            return babel_format(day, "EEEE, d MMMM y", locale=code)
        except Exception:
            return day.strftime("%A, %d %B %Y")


def language_ok(code: str) -> bool:
    return code in LANGUAGES
