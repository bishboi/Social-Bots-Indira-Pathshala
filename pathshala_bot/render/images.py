"""Vertical short-video slides (1080x1920) and the Telegram topic card (1080x1350)."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..config import Exam, settings
from ..models import ContentPackage, Scene
from . import fonts

W, H = 1080, 1920
BG_TOP = (14, 22, 48)
BG_BOTTOM = (48, 28, 92)
WHITE = (255, 255, 255)
MUTED = (190, 196, 220)
SAFFRON = (255, 153, 51)
ACCENTS = {
    "title": SAFFRON, "bullets": (90, 200, 250), "fact": (255, 214, 10), "quiz": (255, 214, 10), "reveal": (52, 199, 89),
    "timeline": SAFFRON, "map": (90, 200, 250), "formula": (255, 105, 180), "comparison": (90, 200, 250),
    "image": SAFFRON, "cta": SAFFRON,
}
KIND_LABEL = {
    "title": "DID YOU KNOW?", "bullets": "KEY POINTS", "fact": "FACT", "quiz": "QUESTION", "reveal": "ANSWER",
    "timeline": "TIMELINE", "map": "ON THE MAP", "formula": "FORMULA", "comparison": "COMPARE", "image": "LOOK",
    "cta": "FOLLOW",
}


def scene_text(sc: Scene) -> str:
    """Flatten any scene kind into on-screen text for a static slide."""
    parts = [sc.body] if sc.body else []
    if sc.kind in ("quiz", "reveal") and sc.options:
        parts.append("\n".join(f"{'ABCD'[i]}. {o}" + (" ✓" if sc.kind == "reveal" and i == sc.correct_index else "") for i, o in enumerate(sc.options[:4])))
    if sc.bullets:
        parts.append("\n".join(f"• {b}" for b in sc.bullets))
    if sc.events:
        parts.append("\n".join(f"{e.date} – {e.label}" for e in sc.events))
    if sc.latex:
        parts.append(sc.latex.replace("\\", "").replace("{", "").replace("}", ""))
    if sc.terms:
        parts.append("\n".join(f"{t.symbol}: {t.meaning}" for t in sc.terms))
    if sc.rows:
        head = f"{sc.left_title or ''} | {sc.right_title or ''}\n" if (sc.left_title or sc.right_title) else ""
        parts.append(head + "\n".join(f"{r.left} | {r.right}" for r in sc.rows))
    return "\n".join(p for p in parts if p).strip()

# Emoji / pictographs: the slide fonts have no glyphs for them.
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")


def clean(text: str) -> str:
    return EMOJI.sub("", text or "").strip()


@lru_cache(maxsize=256)
def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size)


def font_for(text: str, size: int, weight: str, language: str) -> ImageFont.FreeTypeFont:
    return _font(fonts.pick(text, weight, language), size)


def _gradient(w: int, h: int, top=BG_TOP, bottom=BG_BOTTOM) -> Image.Image:
    col = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / (h - 1)
        col.putpixel((0, y), tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    return col.resize((w, h))


def wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        words = para.split()
        if not words:
            lines.append("")
            continue
        cur = words[0]
        for w in words[1:]:
            trial = f"{cur} {w}"
            if draw.textlength(trial, font=font) <= max_w:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
    return lines


def fit_text(draw, text, box_w, box_h, weight, language, start=96, minimum=34, spacing=1.3):
    """Largest font size at which `text` fits the box. Returns (font, lines, line_height)."""
    size = start
    while True:
        font = font_for(text, size, weight, language)
        lines = wrap(draw, text, font, box_w)
        lh = int(size * spacing)
        if len(lines) * lh <= box_h or size <= minimum:
            return font, lines, lh
        size -= 4


def draw_lines(draw, lines, font, x, y, lh, fill, center=False, box_w=0):
    for line in lines:
        lx = x + (box_w - draw.textlength(line, font=font)) / 2 if center else x
        draw.text((lx, y), line, font=font, fill=fill)
        y += lh
    return y


def _chip(draw, xy, text, fill, fg, language, size=34):
    font = font_for(text, size, "bold", language)
    x, y = xy
    tw = draw.textlength(text, font=font)
    draw.rounded_rectangle((x, y, x + tw + 48, y + size + 30), radius=(size + 30) // 2, fill=fill)
    draw.text((x + 24, y + 12), text, font=font, fill=fg)
    return x + tw + 48


def render_slide(slide: Scene, idx: int, total: int, exam: Exam, out: Path) -> Path:
    lang = exam.language
    img = _gradient(W, H)
    d = ImageDraw.Draw(img)
    accent = ACCENTS.get(slide.kind, SAFFRON)

    # header
    _chip(d, (60, 90), exam.short_name, accent, BG_TOP, lang)
    brand_font = font_for(settings.brand_name, 36, "bold", "en")
    bw = d.textlength(settings.brand_name, font=brand_font)
    d.text((W - 60 - bw, 102), settings.brand_name, font=brand_font, fill=WHITE)

    # progress bar
    seg = (W - 120) / total
    for i in range(total):
        d.rounded_rectangle((60 + i * seg + 4, 190, 60 + (i + 1) * seg - 4, 198), radius=4, fill=accent if i <= idx else (80, 86, 120))

    # kind label + heading
    label_font = font_for("A", 40, "bold", "en")
    d.text((80, 330), KIND_LABEL.get(slide.kind, ""), font=label_font, fill=accent)
    heading = clean(slide.heading)
    hfont, hlines, hlh = fit_text(d, heading, W - 160, 420, "bold", lang, start=104, minimum=56, spacing=1.25)
    y = draw_lines(d, hlines, hfont, 80, 400, hlh, WHITE)

    # body card: sized to its text, centred in the space between heading and footer
    if slide.kind == "map" and slide.map:
        from .. import geo

        try:
            png = geo.render_map_png(slide.map, out.with_name(out.stem + "_map.png"), width=960, language=lang)
            m = Image.open(png).convert("RGB")
            img.paste(m, (60, int(y + 60)))
            out.parent.mkdir(parents=True, exist_ok=True)
            _footer(d, accent)
            img.save(out, "PNG", optimize=True)
            return out
        except Exception:
            pass
    body = clean(scene_text(slide))
    pad = 64
    area_top, area_bottom = y + 70, H - 300
    bfont, blines, blh = fit_text(d, body, W - 120 - 2 * pad, area_bottom - area_top - 2 * pad, "regular", lang, start=76, minimum=36, spacing=1.45)
    card_h = max(420, len(blines) * blh + 2 * pad)
    card_top = area_top + max(0, (area_bottom - area_top - card_h) // 3)
    card = (60, card_top, W - 60, card_top + card_h)
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(overlay).rounded_rectangle(card, radius=40, fill=(255, 255, 255, 28))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(card, radius=40, outline=accent, width=4)
    by = card[1] + (card_h - len(blines) * blh) // 2
    draw_lines(d, blines, bfont, card[0] + pad, by, blh, WHITE)

    _footer(d, accent)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG", optimize=True)
    return out


def _footer(d, accent) -> None:
    foot = f"Join Telegram  {settings.brand_handle}"
    ffont = font_for(foot, 40, "bold", "en")
    fw = d.textlength(foot, font=ffont)
    d.rounded_rectangle(((W - fw) / 2 - 40, H - 220, (W + fw) / 2 + 40, H - 140), radius=40, fill=accent)
    d.text(((W - fw) / 2, H - 205), foot, font=ffont, fill=BG_TOP)


def render_slides(pkg: ContentPackage, exam: Exam, out_dir: Path) -> list[Path]:
    total = len(pkg.short.scenes)
    return [render_slide(s, i, total, exam, out_dir / f"slide_{i + 1:02d}.png") for i, s in enumerate(pkg.short.scenes)]


def render_card(pkg: ContentPackage, exam: Exam, out: Path) -> Path:
    """Square-ish 4:5 card for the Telegram post / Instagram feed."""
    cw, ch = 1080, 1350
    lang = exam.language
    img = _gradient(cw, ch)
    d = ImageDraw.Draw(img)
    x = _chip(d, (60, 60), exam.short_name, SAFFRON, BG_TOP, lang)
    _chip(d, (x + 20, 60), clean(pkg.subject)[:28], (90, 200, 250), BG_TOP, lang)

    title = clean(pkg.title)
    tfont, tlines, tlh = fit_text(d, title, cw - 120, 300, "bold", lang, start=84, minimum=48, spacing=1.25)
    y = draw_lines(d, tlines, tfont, 60, 170, tlh, WHITE) + 30
    d.line((60, y, 260, y), fill=SAFFRON, width=8)
    y += 40

    points = [clean(p) for p in pkg.key_points[:6]]
    avail = ch - y - 140
    size = 44
    while size > 28:
        f = font_for(" ".join(points), size, "regular", lang)
        lh = int(size * 1.4)
        wrapped = [wrap(d, p, f, cw - 180) for p in points]
        need = sum(len(w) * lh + 22 for w in wrapped)
        if need <= avail:
            break
        size -= 2
    for lines in wrapped:
        d.ellipse((66, y + lh // 2 - 10, 86, y + lh // 2 + 10), fill=SAFFRON)
        y = draw_lines(d, lines, f, 110, y, lh, WHITE) + 22

    foot_font = font_for("A", 36, "bold", "en")
    d.text((60, ch - 90), f"{settings.brand_name}  |  {settings.brand_handle}", font=foot_font, fill=MUTED)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG", optimize=True)
    return out
