"""Manim renderer for the shorts. Runs in its own process:

    python -m pathshala_bot.render.manim_scene spec.json

The spec (written by manim_video.py) holds the scenes, the duration and voice-over
clip of each scene, pre-fetched images and branding. Every scene's animations are
fitted to its narration length, so voice and visuals stay in sync.
"""

from __future__ import annotations

import json
import math
import shutil
import sys
from pathlib import Path

import manimpango
from manim import (
    BOLD, DOWN, LEFT, ORIGIN, RIGHT, UP, AnimationGroup, Circle, Create, Dot, FadeIn, FadeOut, Flash, GrowFromCenter,
    ImageMobject, Indicate, LaggedStart, Line, MathTex, Polygon, Rectangle, RoundedRectangle, Scene, Text, ValueTracker,
    VGroup, VMobject, always_redraw, Arc, config, rate_functions, tempconfig,
)

from .. import geo

TEX = None


def tex_template():
    global TEX
    if TEX is None:
        from manim import TexTemplate

        TEX = TexTemplate(documentclass=r"\documentclass{article}", preamble=r"\usepackage{amsmath}\usepackage{amssymb}\pagestyle{empty}")
    return TEX
from ..models import MapSpec, Scene as SceneSpec

# ---------- theme ----------
BG1, BG2 = "#0E1630", "#30205C"
WHITE, MUTED = "#FFFFFF", "#BEC4DC"
SAFFRON, SKY, GOLD, GREEN, PINK, RED = "#FF9933", "#5AC8FA", "#FFD60A", "#34C759", "#FF69B4", "#FF453A"
KIND_ACCENT = {"title": SAFFRON, "bullets": SKY, "fact": GOLD, "quiz": GOLD, "reveal": GREEN, "timeline": SAFFRON,
               "map": SKY, "formula": PINK, "comparison": SKY, "image": SAFFRON, "cta": SAFFRON}
KIND_LABEL = {"title": "", "bullets": "KEY POINTS", "fact": "FACT", "quiz": "QUIZ TIME", "reveal": "ANSWER",
              "timeline": "TIMELINE", "map": "ON THE MAP", "formula": "FORMULA", "comparison": "COMPARE",
              "image": "LOOK", "cta": ""}
TOP, BOTTOM = 5.9, -6.1  # content region (frame is 9 x 16 units)
FADE = 0.35


def has_deva(s: str) -> bool:
    return any("ऀ" <= c <= "ॿ" for c in s or "")


class Fonts:
    deva = "Mukta"
    latin = "Poppins"

    def __init__(self, spec):
        for p in spec["font_files"]:
            manimpango.register_font(p)
        self.lang = spec["language"]

    def __call__(self, text: str) -> str:
        return self.deva if (self.lang == "hi" or has_deva(text)) else self.latin


def wrap(text: str, font: str, size: float, max_w: float, weight=BOLD) -> list[str]:
    lines, cur = [], ""
    for para in str(text).split("\n"):
        cur = ""
        for word in para.split():
            trial = f"{cur} {word}".strip()
            if cur and Text(trial, font=font, font_size=size, weight=weight).width > max_w:
                lines.append(cur)
                cur = word
            else:
                cur = trial
        lines.append(cur)
    return [l for l in lines if l] or [""]


class Short(Scene):
    def __init__(self, spec: dict, **kw):
        self.spec = spec
        self.F = Fonts(spec)
        super().__init__(**kw)

    # ---------- helpers ----------
    def txt(self, s, size=40, color=WHITE, weight=BOLD, max_w=8.0, line_gap=0.18, align="left"):
        font = self.F(s)
        lines = wrap(s, font, size, max_w, weight)
        group = VGroup(*[Text(l, font=font, font_size=size, color=color, weight=weight) for l in lines])
        group.arrange(DOWN, buff=line_gap, aligned_edge=LEFT if align == "left" else ORIGIN)
        if group.width > max_w:
            group.scale_to_fit_width(max_w)
        return group

    def clock(self, dur):
        """Animations are clipped to the scene's narration window (absolute renderer time)."""
        end = self.renderer.time + dur - FADE

        def left():
            return max(0.0, end - self.renderer.time)

        def play(*anims, rt=0.8, **kw):
            rt = min(rt, left())
            if rt < 0.08:
                self.add(*[a.mobject for a in anims if hasattr(a, "mobject")])
                return
            self.play(*anims, run_time=rt, **kw)

        def hold(seconds):
            seconds = min(seconds, left())
            if seconds > 0.02:
                self.wait(seconds)

        def finish(group):
            hold(left())
            self.play(FadeOut(group), run_time=FADE)

        self._hold = hold
        return play, left, finish

    def header_label(self, kind, accent):
        label = self.spec["labels"].get(kind, KIND_LABEL.get(kind, ""))
        if not label:
            return VGroup()
        t = Text(label, font=self.F(label), font_size=30, color=accent, weight=BOLD)
        return t.to_edge(LEFT, buff=0.55).set_y(TOP - 0.1)

    def heading(self, text, accent, y=None, size=72):
        h = self.txt(text, size=size, max_w=8.0)
        h.to_edge(LEFT, buff=0.55).set_y((y if y is not None else TOP - 0.75) - h.height / 2)
        return h

    # ---------- chrome ----------
    def chrome(self):
        bg = Rectangle(width=9.2, height=16.2, stroke_width=0).set_fill([BG1, BG2], opacity=1)
        bg.set_sheen_direction(DOWN)
        bg.z_index = -20
        self.add(bg)
        top_band = Rectangle(width=9.2, height=1.9, stroke_width=0).set_fill(BG1, 1).move_to([0, 7.1, 0])
        bot_band = Rectangle(width=9.2, height=1.7, stroke_width=0).set_fill(BG2, 1).move_to([0, -7.2, 0])
        # Header carries the brand only (no exam name).
        brand = Text(self.spec["brand"], font="Poppins", font_size=34, color=WHITE, weight=BOLD).move_to([0, 7.15, 0])
        cta_t = Text(f"Join Telegram  {self.spec['handle']}", font="Poppins", font_size=30, color=BG1, weight=BOLD)
        cta = RoundedRectangle(corner_radius=0.3, width=cta_t.width + 0.7, height=0.62, stroke_width=0).set_fill(SAFFRON, 1)
        cta_t.move_to(cta)
        cta_g = VGroup(cta, cta_t).move_to([0, -7.0, 0])
        n = len(self.spec["scenes"])
        seg = 8.0 / n
        self.bars = VGroup(*[Line([-4 + i * seg + 0.05, 6.05, 0], [-4 + (i + 1) * seg - 0.05, 6.05, 0], stroke_width=6, color="#50567A") for i in range(n)])
        top_band.set_z_index(9)
        bot_band.set_z_index(9)
        for m in (brand, cta_g, self.bars):
            m.set_z_index(11)
        self.add(top_band, bot_band, brand, cta_g, self.bars)

    # ---------- scene kinds ----------
    def s_title(self, s, dur, accent):
        play, left, finish = self.clock(dur)
        h = self.txt(s["heading"], size=100, max_w=8.2, align="center").move_to([0, 1.4, 0])
        bar = Line([-1.5, 0, 0], [1.5, 0, 0], color=accent, stroke_width=10).next_to(h, DOWN, buff=0.45)
        body = self.txt(s["body"], size=54, color=MUTED, weight="NORMAL", max_w=7.8, align="center").next_to(bar, DOWN, buff=0.5) if s["body"] else VGroup()
        ring = Circle(radius=3.2, color=accent, stroke_width=3, stroke_opacity=0.35).move_to(h)
        play(Create(ring), FadeIn(h, shift=UP * 0.4, scale=0.9), rt=0.9)
        play(Create(bar), FadeIn(body, shift=UP * 0.2), rt=0.6)
        play(ring.animate.scale(1.15).set_stroke(opacity=0.1), rt=min(1.5, left()))
        finish(VGroup(h, bar, body, ring))

    def s_bullets(self, s, dur, accent):
        play, left, finish = self.clock(dur)
        lab, h = self.header_label("bullets", accent), self.heading(s["heading"], accent)
        items = VGroup()
        for b in s["bullets"][:5]:
            t = self.txt(b, size=56, weight="NORMAL", max_w=7.2)
            dot = Dot(radius=0.11, color=accent).next_to(t[0], LEFT, buff=0.3)
            items.add(VGroup(dot, t))
        items.arrange(DOWN, buff=0.7, aligned_edge=LEFT).next_to(h, DOWN, buff=0.8).to_edge(LEFT, buff=0.95)
        if s["body"] and not items:
            items = self.txt(s["body"], size=48, weight="NORMAL").next_to(h, DOWN, buff=0.8).to_edge(LEFT, buff=0.55)
        play(FadeIn(lab), FadeIn(h, shift=RIGHT * 0.3), rt=0.6)
        per = max(0.4, (left() * 0.75) / max(1, len(items)))
        for it in items:
            play(FadeIn(it, shift=RIGHT * 0.4), rt=min(0.6, per))
            self._hold(per - 0.6)
        finish(VGroup(lab, h, items))

    def s_fact(self, s, dur, accent):
        play, left, finish = self.clock(dur)
        lab, h = self.header_label("fact", accent), self.heading(s["heading"], accent)
        big_txt = (s["bullets"] or [s["body"]])[0]
        big = self.txt(big_txt, size=150, color=accent, max_w=8.0, align="center").move_to([0, 0.6, 0])
        rest = " ".join(s["bullets"][1:]) or (s["body"] if s["bullets"] else "")
        sub = self.txt(rest, size=56, color=WHITE, weight="NORMAL", max_w=7.6, align="center").next_to(big, DOWN, buff=0.6) if rest else VGroup()
        play(FadeIn(lab), FadeIn(h), rt=0.5)
        play(GrowFromCenter(big), rt=0.7)
        play(Flash(big.get_center(), color=accent, line_length=0.6, num_lines=14, flash_radius=big.width / 2 + 0.3), FadeIn(sub, shift=UP * 0.2), rt=0.8)
        finish(VGroup(lab, h, big, sub))

    def _options(self, s, accent):
        boxes = VGroup()
        for i, o in enumerate(s["options"][:4]):
            t = self.txt(f"{'ABCD'[i]}.  {o}", size=52, weight="NORMAL", max_w=7.2)
            box = RoundedRectangle(corner_radius=0.25, width=8.0, height=max(1.25, t.height + 0.55), stroke_color=accent, stroke_width=3).set_fill(WHITE, 0.06)
            t.move_to(box).align_to(box, LEFT).shift(RIGHT * 0.35)
            boxes.add(VGroup(box, t))
        return boxes.arrange(DOWN, buff=0.35)

    def s_quiz(self, s, dur, accent):
        play, left, finish = self.clock(dur)
        lab = self.header_label("quiz", accent)
        q = self.txt(s["body"] or s["heading"], size=62, max_w=8.0).to_edge(LEFT, buff=0.55)
        q.set_y(TOP - 0.7 - q.height / 2)
        opts = self._options(s, accent).next_to(q, DOWN, buff=0.6).set_x(0)
        room = opts.get_top()[1] - (BOTTOM + 2.2)
        if opts.height > room:
            opts.scale_to_fit_height(room).next_to(q, DOWN, buff=0.6).set_x(0)
        play(FadeIn(lab), FadeIn(q, shift=DOWN * 0.2), rt=0.6)
        play(LaggedStart(*[FadeIn(b, shift=LEFT * 0.4) for b in opts], lag_ratio=0.25), rt=1.2)
        # countdown ring for the rest of the scene
        t = ValueTracker(1.0)
        center = [0, opts.get_bottom()[1] - 1.15, 0]
        total = max(1.0, left() - 0.05)
        secs = int(math.ceil(total))
        ring = always_redraw(lambda: Arc(radius=0.85, start_angle=math.pi / 2, angle=-2 * math.pi * max(0.001, t.get_value()), color=accent, stroke_width=12).move_arc_center_to(center))
        num = always_redraw(lambda: Text(str(max(0, math.ceil(t.get_value() * secs))), font="Poppins", font_size=56, color=WHITE, weight=BOLD).move_to(center))
        self.add(ring, num)
        play(t.animate.set_value(0), rt=total, rate_func=rate_functions.linear)
        finish(VGroup(lab, q, opts, ring, num))

    def s_reveal(self, s, dur, accent):
        play, left, finish = self.clock(dur)
        lab = self.header_label("reveal", GREEN)
        h = self.heading(s["heading"], GREEN)
        idx = s["correct_index"] if s["correct_index"] is not None else -1
        opts = self._options(s, MUTED).next_to(h, DOWN, buff=0.6).set_x(0) if s["options"] else VGroup()
        play(FadeIn(lab), FadeIn(h), FadeIn(opts), rt=0.6)
        if 0 <= idx < len(opts):
            right = opts[idx]
            others = [o for i, o in enumerate(opts) if i != idx]
            play(right[0].animate.set_fill(GREEN, 0.35).set_stroke(GREEN, 5), *[o.animate.set_opacity(0.3) for o in others], rt=0.6)
            play(Indicate(right, color=GREEN, scale_factor=1.05), rt=0.6)
        body = self.txt(s["body"], size=50, color=MUTED, weight="NORMAL", max_w=8.0).next_to(opts if len(opts) else h, DOWN, buff=0.6).to_edge(LEFT, buff=0.55) if s["body"] else VGroup()
        play(FadeIn(body, shift=UP * 0.2), rt=0.5)
        finish(VGroup(lab, h, opts, body))

    def s_timeline(self, s, dur, accent):
        play, left, finish = self.clock(dur)
        lab, h = self.header_label("timeline", accent), self.heading(s["heading"], accent)
        events = s["events"][:6]
        x0 = -3.5
        rows = VGroup()
        for ev in events:
            dot = Dot(radius=0.17, color=accent)
            date = Text(ev["date"], font=self.F(ev["date"]), font_size=46, color=accent, weight=BOLD)
            label = self.txt(ev["label"], size=44, weight="NORMAL", max_w=6.9)
            text = VGroup(date, label).arrange(DOWN, buff=0.12, aligned_edge=LEFT)
            dot.next_to(date, LEFT, buff=0.35)
            rows.add(VGroup(dot, text))
        avail = h.get_bottom()[1] - 0.8 - (BOTTOM + 0.2)
        rows.arrange(DOWN, buff=0.5, aligned_edge=LEFT)
        if rows.height > avail:
            rows.scale_to_fit_height(avail)
        rows.next_to(h, DOWN, buff=0.8).set_x(rows.get_x() - rows[0][0].get_x() + x0)
        line = Line(rows[0][0].get_center(), rows[-1][0].get_center(), color=accent, stroke_width=6, stroke_opacity=0.7)
        play(FadeIn(lab), FadeIn(h), rt=0.5)
        play(Create(line), rt=0.6)
        per = max(0.35, left() * 0.8 / max(1, len(rows)))
        for row in rows:
            play(GrowFromCenter(row[0]), FadeIn(row[1], shift=RIGHT * 0.3), rt=min(0.6, per))
            self._hold(per - 0.6)
        finish(VGroup(lab, h, line, rows))

    def s_formula(self, s, dur, accent):
        play, left, finish = self.clock(dur)
        lab, h = self.header_label("formula", accent), self.heading(s["heading"], accent)
        formula = None
        if s.get("latex") and self.spec["latex"]:
            try:
                formula = MathTex(s["latex"], color=WHITE, tex_template=tex_template()).scale(1.9)
            except Exception:
                formula = None
        if formula is None:
            plain = (s.get("latex") or s["body"]).replace("\\", "").replace("{", "").replace("}", "")
            formula = Text(plain, font="Poppins", font_size=64, color=WHITE)
        if formula.width > 8.0:
            formula.scale_to_fit_width(8.0)
        box = RoundedRectangle(corner_radius=0.3, width=8.4, height=formula.height + 1.0, stroke_color=accent, stroke_width=4).set_fill(WHITE, 0.06)
        fg = VGroup(box, formula.move_to(box)).next_to(h, DOWN, buff=0.7).set_x(0)
        terms = VGroup()
        for tm in s["terms"][:5]:
            sym = Text(tm["symbol"], font="Poppins", font_size=50, color=accent, weight=BOLD)
            mean = self.txt(tm["meaning"], size=46, weight="NORMAL", max_w=6.2)
            terms.add(VGroup(sym, mean.next_to(sym, RIGHT, buff=0.4)))
        terms.arrange(DOWN, buff=0.38, aligned_edge=LEFT).next_to(fg, DOWN, buff=0.7).to_edge(LEFT, buff=0.75)
        play(FadeIn(lab), FadeIn(h), rt=0.5)
        play(Create(box), rt=0.4)
        from manim import Write

        play(Write(formula), rt=min(1.6, max(0.6, left() * 0.35)))
        for t in terms:
            play(FadeIn(t, shift=UP * 0.2), rt=min(0.5, max(0.2, left() * 0.15)))
        finish(VGroup(lab, h, fg, terms))

    def s_comparison(self, s, dur, accent):
        play, left, finish = self.clock(dur)
        lab, h = self.header_label("comparison", accent), self.heading(s["heading"], accent)
        lt = self.txt(s.get("left_title") or "", size=46, color=BG1, max_w=3.6, align="center")
        rt_ = self.txt(s.get("right_title") or "", size=46, color=BG1, max_w=3.6, align="center")
        hh = max(lt.height, rt_.height) + 0.4
        lh = RoundedRectangle(corner_radius=0.2, width=3.95, height=hh, stroke_width=0).set_fill(SAFFRON, 1)
        rh = RoundedRectangle(corner_radius=0.2, width=3.95, height=hh, stroke_width=0).set_fill(SKY, 1)
        head = VGroup(VGroup(lh, lt.move_to(lh)), VGroup(rh, rt_.move_to(rh))).arrange(RIGHT, buff=0.1).next_to(h, DOWN, buff=0.6).set_x(0)
        rows = VGroup()
        for r in s["rows"][:5]:
            a = self.txt(r["left"], size=42, weight="NORMAL", max_w=3.6, align="center")
            b = self.txt(r["right"], size=42, weight="NORMAL", max_w=3.6, align="center")
            hgt = max(a.height, b.height) + 0.45
            ca = Rectangle(width=3.95, height=hgt, stroke_color="#50567A", stroke_width=2).set_fill(WHITE, 0.05)
            cb = ca.copy()
            rows.add(VGroup(VGroup(ca, a.move_to(ca)), VGroup(cb, b.move_to(cb))).arrange(RIGHT, buff=0.1))
        rows.arrange(DOWN, buff=0.1).next_to(head, DOWN, buff=0.1).set_x(0)
        play(FadeIn(lab), FadeIn(h), rt=0.5)
        play(FadeIn(head, shift=DOWN * 0.2), rt=0.5)
        per = max(0.3, left() * 0.7 / max(1, len(rows)))
        for r in rows:
            play(FadeIn(r, shift=UP * 0.2), rt=min(0.5, per))
            self._hold(per - 0.5)
        finish(VGroup(lab, h, head, rows))

    def s_image(self, s, dur, accent, media):
        if not media:
            return self.s_bullets({**s, "bullets": s["bullets"] or ([s["body"]] if s["body"] else [])}, dur, accent)
        play, left, finish = self.clock(dur)
        lab, h = self.header_label("image", accent), self.heading(s["heading"], accent)
        img = ImageMobject(media["path"])
        img.scale_to_fit_width(8.2)
        if img.height > 7.2:
            img.scale_to_fit_height(7.2)
        img.next_to(h, DOWN, buff=0.5)
        frame = Rectangle(width=img.width + 0.1, height=img.height + 0.1, stroke_color=accent, stroke_width=5).move_to(img)
        credit = Text(media["credit"][:90], font="Poppins", font_size=16, color=MUTED).next_to(frame, DOWN, buff=0.12).align_to(frame, LEFT)
        body = self.txt(s["body"], size=38, weight="NORMAL", max_w=8.0).next_to(credit, DOWN, buff=0.35).to_edge(LEFT, buff=0.55) if s["body"] else VGroup()
        play(FadeIn(lab), FadeIn(h), rt=0.5)
        play(FadeIn(img, scale=1.05), Create(frame), FadeIn(credit), rt=0.7)
        play(FadeIn(body, shift=UP * 0.2), img.animate.scale(1.04), frame.animate.scale(1.04), rt=min(2.5, max(0.3, left())))
        finish_group = [lab, h, frame, credit, body]
        self._hold(left())
        self.play(*[FadeOut(m) for m in finish_group], FadeOut(img), run_time=FADE)

    def s_map(self, s, dur, accent):
        play, left, finish = self.clock(dur)
        lab, h = self.header_label("map", accent), self.heading(s["heading"], accent, size=64)
        spec = MapSpec.model_validate(s["map"] or {})
        rm = geo.resolve(spec)
        box_top = h.get_bottom()[1] - 0.35
        box_bottom = BOTTOM + 0.55
        box_h, box_w = box_top - box_bottom, 8.6
        box_center = [0, (box_top + box_bottom) / 2, 0]
        india = geo.Projection(geo.INDIA_BBOX, box_w, box_h)

        def to_pts(ring, proj, k=1.0, c=(0, 0), shift=(0, 0)):
            out = []
            for lon, lat in ring:
                x, y = proj(lon, lat)
                out.append([(x - c[0]) * k + shift[0], (y - c[1]) * k + shift[1], 0])
            return out

        palette = [SAFFRON, SKY, PINK, GOLD, GREEN, "#BF5AF2"]
        hs_color = {x["name"]: palette[i % len(palette)] for i, x in enumerate(rm.highlight_states)}
        focus_name = rm.focus["name"] if rm.focus else None
        state_polys = VGroup()
        for st in geo.states():
            if st["name"] in hs_color:
                fill, op, stroke = hs_color[st["name"]], 0.85, BG1
            elif st["name"] == focus_name:
                fill, op, stroke = (SAFFRON, 0.85, BG1) if not rm.focus_districts else ("#5A64A8", 0.6, "#8A90B8")
            else:
                fill, op, stroke = "#3A4170", 0.55, "#8A90B8"
            for ring in st["rings"]:
                if len(ring) < 3:
                    continue
                p = Polygon(*to_pts(ring, india, shift=box_center[:2]), stroke_color=stroke, stroke_width=1.6)
                p.set_fill(fill, op)
                p.state_name = st["name"]
                state_polys.add(p)
        mask = Rectangle(width=9.4, height=6.2 - box_top + 0.05, stroke_width=0).set_fill(BG1, 1)
        mask.move_to([0, (6.2 + box_top) / 2, 0]).set_z_index(5)
        mask_b = Rectangle(width=9.4, height=box_bottom - (-8.2), stroke_width=0).set_fill(BG2, 1)
        mask_b.move_to([0, (box_bottom - 8.2) / 2, 0]).set_z_index(5)
        lab.set_z_index(6)
        h.set_z_index(6)
        self.add(mask, mask_b)
        play(FadeIn(lab), FadeIn(h), rt=0.4)
        play(FadeIn(state_polys, scale=0.95), rt=0.9)

        # zoom into the focus state
        view_proj, k, c = india, 1.0, (0.0, 0.0)
        if rm.focus:
            x0, y0, x1, y1 = rm.view_box
            ax, ay = india(x0, y0)
            bx, by = india(x1, y1)
            k = min(box_w / (bx - ax), box_h / (by - ay)) * 0.9
            c = ((ax + bx) / 2, (ay + by) / 2)
            target = VGroup(*state_polys)
            anims = [target.animate.shift(-(c[0] + box_center[0]) * RIGHT - (c[1] + box_center[1]) * UP).scale(k, about_point=ORIGIN).shift(box_center[0] * RIGHT + box_center[1] * UP)]
            play(*anims, rt=1.2, rate_func=rate_functions.smooth)
            for p in state_polys:
                if p.state_name != focus_name:
                    p.set_fill(opacity=0.18).set_stroke(opacity=0.35)
                else:
                    p.set_fill("#2B3466", 0.95).set_stroke(SAFFRON, 5)

        def P(lon, lat):
            x, y = india(lon, lat)
            return [(x - c[0]) * k + box_center[0], (y - c[1]) * k + box_center[1], 0]

        layers = VGroup()
        hd_names = {d["name"] for d in rm.highlight_districts}
        if rm.focus_districts:
            dist = VGroup()
            for d in rm.focus_districts:
                for ring in d["rings"]:
                    if len(ring) >= 3:
                        poly = Polygon(*[P(*pt) for pt in ring], stroke_color="#8A90B8", stroke_width=1.5)
                        poly.set_fill(SAFFRON if d["name"] in hd_names else BG1, 0.9 if d["name"] in hd_names else 0.0)
                        dist.add(poly)
            play(Create(dist), rt=0.8)
            layers.add(dist)
        rv = VGroup()
        for r in rm.rivers:
            for line in r["lines"]:
                for seg in geo.clip_line(line, rm.view_box, margin=0.2 if rm.focus else 1.0):
                    m = VMobject(stroke_color="#4FA8FF", stroke_width=5)
                    m.set_points_smoothly([P(*pt) for pt in seg]) if len(seg) > 2 else m.set_points_as_corners([P(*pt) for pt in seg])
                    rv.add(m)
        if len(rv):
            play(Create(rv), rt=0.9)
            layers.add(rv)
        pins = VGroup()
        per = max(0.35, left() * 0.75 / max(1, len(rm.points)))
        dots = [Dot(P(pt.lon, pt.lat), radius=0.16, color=RED).set_stroke(WHITE, 3) for pt in rm.points]
        boxes = [(d.get_left()[0], d.get_bottom()[1], d.get_right()[0], d.get_top()[1]) for d in dots]

        def clash(m):
            l, b, r, t = m.get_left()[0], m.get_bottom()[1], m.get_right()[0], m.get_top()[1]
            if l < -4.4 or r > 4.4 or b < box_bottom or t > box_top:
                return True
            return any(not (r < x0 or l > x1 or t < y0 or b > y1) for x0, y0, x1, y1 in boxes)

        for pt, dot in zip(rm.points, dots):
            pos = dot.get_center()
            label = Text(pt.label, font=self.F(pt.label), font_size=40, color=WHITE, weight=BOLD)
            label.set_stroke(BG1, width=7, background=True)
            leader = VGroup()
            dirs = (RIGHT, LEFT, UP, DOWN, UP + RIGHT, DOWN + RIGHT, UP + LEFT, DOWN + LEFT)
            done = False
            for buff in (0.12, 0.5, 0.9, 1.3):
                for d in dirs:
                    label.next_to(dot, d, buff=buff)
                    if not clash(label):
                        done = True
                        break
                if done:
                    break
            if not done:  # crowded: put it inside the map box, nearest the centre
                label.next_to(dot, LEFT if pos[0] > 0 else RIGHT, buff=0.6)
            if done and buff > 0.12 or not done:
                leader = Line(dot.get_center(), label.get_critical_point(-(label.get_center() - dot.get_center())), color=WHITE, stroke_width=2)
            boxes.append((label.get_left()[0], label.get_bottom()[1], label.get_right()[0], label.get_top()[1]))
            pins.add(VGroup(dot, label, leader))
            play(GrowFromCenter(dot), FadeIn(label, shift=UP * 0.15), FadeIn(leader), Flash(pos, color=GOLD, line_length=0.25, flash_radius=0.3), rt=min(0.7, per))
            self._hold(per - 0.7)
        attr = Text(geo.ATTRIBUTION, font="Poppins", font_size=14, color=MUTED).move_to([0, box_bottom - 0.3, 0])
        self.add(attr)
        attr.set_z_index(6)
        finish(VGroup(lab, h, state_polys, layers, pins, attr, mask, mask_b))

    def s_cta(self, s, dur, accent):
        play, left, finish = self.clock(dur)
        h = self.txt(s["heading"], size=92, max_w=8.2, align="center").move_to([0, 2.4, 0])
        body = self.txt(s["body"] or "", size=54, color=MUTED, weight="NORMAL", max_w=7.6, align="center").next_to(h, DOWN, buff=0.5) if s["body"] else VGroup()
        handle = Text(self.spec["handle"], font="Poppins", font_size=60, color=accent, weight=BOLD)
        handle.next_to(body if len(body) else h, DOWN, buff=1.0)
        pill = RoundedRectangle(corner_radius=0.5, width=handle.width + 1.0, height=1.1, stroke_color=accent, stroke_width=5).move_to(handle)
        play(FadeIn(h, shift=UP * 0.3), FadeIn(body), rt=0.7)
        play(Create(pill), FadeIn(handle, scale=1.2), rt=0.7)
        play(Indicate(VGroup(pill, handle), color=accent, scale_factor=1.08), rt=min(1.0, max(0.2, left())))
        finish(VGroup(h, body, handle, pill))

    # ---------- main ----------
    def construct(self):
        self.chrome()
        for i, (s, plan) in enumerate(zip(self.spec["scenes"], self.spec["plan"])):
            accent = KIND_ACCENT.get(s["kind"], SAFFRON)
            self.bars[i].set_color(accent)
            if plan.get("audio"):
                self.add_sound(plan["audio"])
            dur = plan["seconds"]
            kind = s["kind"]
            fn = getattr(self, f"s_{kind}", self.s_bullets)
            if kind == "image":
                fn(s, dur, accent, plan.get("media"))
            else:
                fn(s, dur, accent)


def main(spec_path: str) -> None:
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    out_dir = Path(spec["out_dir"])
    with tempconfig({
        "pixel_width": 1080, "pixel_height": 1920, "frame_width": 9.0, "frame_height": 16.0,
        "frame_rate": spec.get("fps", 30), "media_dir": str(out_dir / "manim_media"), "output_file": "short_manim",
        "disable_caching": True, "verbosity": "WARNING", "progress_bar": "none", "background_color": BG1,
    }):
        scene = Short(spec)
        scene.render()
    found = sorted((out_dir / "manim_media").rglob("short_manim.mp4"))
    if not found:
        raise SystemExit("manim produced no video")
    shutil.move(str(found[-1]), out_dir / "short.mp4")


if __name__ == "__main__":
    main(sys.argv[1])
