"""Accurate map data: India's official boundaries (DataMeet), rivers (Natural Earth),
places (GeoNames), with Wikipedia coordinates as a fallback geocoder.

The model only *names* places; every coordinate comes from these datasets.
Unresolved names are dropped (and logged) rather than guessed.
"""

from __future__ import annotations

import difflib
import json
import logging
import math
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .config import PROJECT_DIR
from .models import MapPlace, MapSpec

log = logging.getLogger(__name__)

GEO_DIR = PROJECT_DIR / "assets" / "geo"
ATTRIBUTION = "Map: DataMeet India (CC BY 4.0), Natural Earth, GeoNames (CC BY 4.0)"
INDIA_BBOX = (68.0, 6.5, 97.5, 37.2)

# Generic words ignored when matching place names.
GENERIC = {
    "national", "park", "wildlife", "sanctuary", "tiger", "reserve", "lake", "fort", "fortress", "dam", "the",
    "mount", "mt", "hill", "hills", "city", "district", "river", "palace", "temple", "of", "jheel", "jhil", "sagar",
    "garh", "qila", "site", "archaeological", "bird",
}
KIND_FCODES = {
    "park": {"PRK", "RESN", "RESW", "NP", "RESF"},
    "fort": {"FT", "PAL", "MNMT"},
    "lake": {"LK", "LKS", "RSV", "LKI", "RSVI", "RSVT"},
    "dam": {"DAM", "RSV"},
    "mountain": {"MT", "PK", "MTS", "HLL", "HLLS"},
    "city": {"PPLC", "PPLA", "PPLA2", "PPLA3", "PPL"},
}
DISTRICT_ALIASES = {
    "dholpur": "dhaulpur", "chittorgarh": "chittaurgarh", "jhunjhunu": "jhunjhunun", "sri ganganagar": "ganganagar",
    "shri ganganagar": "ganganagar", "gurugram": "gurgaon", "prayagraj": "allahabad", "ayodhya": "faizabad",
}
RIVER_ALIASES = {"ganga": "ganges", "mahanadi": "mahananadi", "kaveri": "cauvery", "ghaghara": "ghaghara"}


def norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9 ]+", " ", t).strip()


def tokens(text: str) -> set[str]:
    return {w for w in norm(text).split() if w not in GENERIC} or set(norm(text).split())


@lru_cache(maxsize=None)
def _load(name: str):
    return json.loads((GEO_DIR / name).read_text(encoding="utf-8"))


def states() -> list[dict]:
    return _load("india_states.json")


def districts() -> list[dict]:
    return _load("india_districts_2011.json")


def rivers() -> list[dict]:
    return _load("rivers.json")


@lru_cache(maxsize=None)
def places() -> list[dict]:
    d = _load("places.json")
    cols = d["columns"]
    return [dict(zip(cols, r)) for r in d["rows"]]


def _best(name: str, candidates: list[str], cutoff: float = 0.78) -> str | None:
    by_norm = {norm(c).replace(" ", ""): c for c in candidates}
    key = norm(name).replace(" ", "")
    if key in by_norm:
        return by_norm[key]
    m = difflib.get_close_matches(key, list(by_norm), n=1, cutoff=cutoff)
    return by_norm[m[0]] if m else None


def find_state(name: str) -> dict | None:
    alias = {"j&k": "Jammu & Kashmir", "jammu and kashmir": "Jammu & Kashmir", "andaman and nicobar islands": "Andaman & Nicobar", "orissa": "Odisha", "nct of delhi": "Delhi"}
    name = alias.get(name.lower().strip(), name)
    hit = _best(name, [s["name"] for s in states()])
    return next((s for s in states() if s["name"] == hit), None) if hit else None


def find_district(name: str, state: str | None) -> dict | None:
    pool = [d for d in districts() if not state or norm(d["state"]) == norm(state)]
    key = DISTRICT_ALIASES.get(norm(name), name)
    hit = _best(key, [d["name"] for d in pool], cutoff=0.8)
    return next((d for d in pool if d["name"] == hit), None) if hit else None


def find_river(name: str) -> list[dict]:
    key = norm(RIVER_ALIASES.get(norm(name), name)).replace(" ", "")
    return [r for r in rivers() if norm(r["name"]).replace(" ", "") == key]


@dataclass
class Point:
    name: str
    label: str
    kind: str
    lat: float
    lon: float
    source: str


def _gazetteer(place: MapPlace, state_hint: str | None) -> tuple[Point, bool] | None:
    """Best GeoNames match and whether its feature type fits the requested kind."""
    want = tokens(place.name)
    alt = tokens(" ".join(DISTRICT_ALIASES.get(w, w) for w in norm(place.name).split()))
    if not want:
        return None
    best, best_score = None, -1e9
    for p in places():
        have = tokens(p["name"]) | tokens(p["ascii"])
        if not (want <= have or alt <= have):
            continue
        score = 0.0
        if norm(p["name"]) == norm(place.name) or norm(p["ascii"]) == norm(place.name):
            score += 5
        if state_hint and norm(p["state"]) == norm(state_hint):
            score += 4
        if p["fcode"] in KIND_FCODES.get(place.kind, set()):
            score += 3
        if p["fcode"].startswith("ADM"):
            score -= 4  # administrative-area centroids are not the town / site itself
        score += math.log10(p["population"] + 10) * 0.5 - len(have - want) * 0.7
        if score > best_score:
            best, best_score = p, score
    if best is None:
        return None
    fits = place.kind in ("other", "site") or best["fcode"] in KIND_FCODES.get(place.kind, set())
    return Point(place.name, place.label, place.kind, best["lat"], best["lon"], "GeoNames"), fits


def geocode(place: MapPlace, state_hint: str | None = None) -> Point | None:
    hit = _gazetteer(place, state_hint)
    pt, fits = hit if hit else (None, False)
    if pt is None or not fits:
        # e.g. "Sambhar Lake" only matched the town of Sambhar: the article's own coordinates are better
        from . import resources

        info = resources.wiki_summary(place.name)
        coords = (info or {}).get("coordinates")
        if coords and INDIA_BBOX[0] <= coords["lon"] <= INDIA_BBOX[2] and INDIA_BBOX[1] <= coords["lat"] <= INDIA_BBOX[3]:
            pt = Point(place.name, place.label, place.kind, coords["lat"], coords["lon"], "Wikipedia")
    if pt and state_hint:  # sanity check: must lie inside/near the focus state
        st = find_state(state_hint)
        if st:
            x0, y0, x1, y1 = bbox(st["rings"])
            if not (x0 - 0.5 <= pt.lon <= x1 + 0.5 and y0 - 0.5 <= pt.lat <= y1 + 0.5):
                log.warning("dropping %s: %.3f,%.3f is outside %s", place.name, pt.lat, pt.lon, state_hint)
                return None
    if pt is None:
        log.warning("could not geocode %r - leaving it off the map", place.name)
    return pt


def bbox(rings: list[list[list[float]]]) -> tuple[float, float, float, float]:
    xs = [x for r in rings for x, _ in r]
    ys = [y for r in rings for _, y in r]
    return min(xs), min(ys), max(xs), max(ys)


class Projection:
    """Equirectangular projection scaled by cos(latitude) to fit a target box."""

    def __init__(self, box: tuple[float, float, float, float], width: float, height: float, pad: float = 0.04):
        x0, y0, x1, y1 = box
        self.lon0, self.lat0 = (x0 + x1) / 2, (y0 + y1) / 2
        self.k = math.cos(math.radians(self.lat0))
        w, h = (x1 - x0) * self.k, (y1 - y0)
        self.s = min(width / w, height / h) * (1 - 2 * pad)

    def __call__(self, lon: float, lat: float) -> tuple[float, float]:
        return (lon - self.lon0) * self.k * self.s, (lat - self.lat0) * self.s


@dataclass
class ResolvedMap:
    view_box: tuple[float, float, float, float]
    focus: dict | None
    highlight_states: list[dict]
    focus_districts: list[dict]
    highlight_districts: list[dict]
    rivers: list[dict]
    points: list[Point]


def resolve(spec: MapSpec) -> ResolvedMap:
    focus = find_state(spec.focus_state) if spec.focus_state else None
    if spec.focus_state and not focus:
        log.warning("unknown focus state %r - showing India", spec.focus_state)
    view = bbox(focus["rings"]) if focus else INDIA_BBOX
    hs = [s for s in (find_state(n) for n in spec.highlight_states) if s]
    fd = [d for d in districts() if focus and norm(d["state"]) == norm(focus["name"])]
    hd = []
    for n in spec.highlight_districts:
        d = find_district(n, focus["name"] if focus else None)
        if d:
            hd.append(d)
        else:
            log.warning("district %r not in Census-2011 boundaries - skipped", n)
    rv = [r for n in spec.rivers for r in find_river(n)]
    pts = [p for p in (geocode(pl, focus["name"] if focus else None) for pl in spec.places) if p]
    return ResolvedMap(view, focus, hs, fd, hd, rv, pts)


def clip_line(line, box, margin=0.3):
    """Split a polyline into the pieces that fall inside box (+margin)."""
    x0, y0, x1, y1 = box[0] - margin, box[1] - margin, box[2] + margin, box[3] + margin
    out, cur = [], []
    for x, y in line:
        if x0 <= x <= x1 and y0 <= y <= y1:
            cur.append((x, y))
        elif cur:
            if len(cur) > 1:
                out.append(cur)
            cur = []
    if len(cur) > 1:
        out.append(cur)
    return out


def render_map_png(spec: MapSpec, out: Path, title: str = "", width: int = 1400, language: str = "en") -> Path | None:
    """Static map for PDFs / slideshow fallback (Pillow)."""
    from PIL import Image, ImageDraw, ImageFont

    from .render import fonts

    rm = resolve(spec)
    # Canvas follows the shape of the area shown, so there is no empty margin around it.
    x0, y0, x1, y1 = rm.view_box
    aspect = (y1 - y0) / ((x1 - x0) * math.cos(math.radians((y0 + y1) / 2)))
    top, bottom = (64 if title else 16), 40
    map_h = int(width * min(1.2, max(0.5, aspect)))
    height = map_h + top + bottom
    img = Image.new("RGB", (width, height), (247, 248, 252))
    d = ImageDraw.Draw(img)
    proj = Projection(rm.view_box, width, map_h, pad=0.03)

    def P(lon, lat):
        x, y = proj(lon, lat)
        return width / 2 + x, top + map_h / 2 - y

    hs_names = {s["name"] for s in rm.highlight_states}
    for s in states():
        fill = (255, 214, 170) if s["name"] in hs_names else ((226, 232, 245) if not rm.focus or s is rm.focus else (236, 238, 243))
        if rm.focus and s is rm.focus:
            fill = (255, 236, 214) if not hs_names else fill
        for r in s["rings"]:
            d.polygon([P(*pt) for pt in r], fill=fill, outline=(150, 156, 180))
    hd_names = {x["name"] for x in rm.highlight_districts}
    for dist in rm.focus_districts:
        for r in dist["rings"]:
            d.polygon([P(*pt) for pt in r], fill=(255, 160, 80) if dist["name"] in hd_names else None, outline=(200, 170, 140))
    if rm.focus:
        for r in rm.focus["rings"]:
            d.line([P(*pt) for pt in r] + [P(*r[0])], fill=(14, 22, 48), width=4)
    for rv in rm.rivers:
        for line in rv["lines"]:
            for seg in clip_line(line, rm.view_box):
                d.line([P(*pt) for pt in seg], fill=(40, 120, 220), width=4)
    for pt in rm.points:
        x, y = P(pt.lon, pt.lat)
        d.ellipse((x - 11, y - 11, x + 11, y + 11), fill=(220, 40, 60), outline="white", width=3)
        f = ImageFont.truetype(str(fonts.pick(pt.label, "bold", language)), 30)
        d.text((x + 16, y - 20), pt.label, font=f, fill=(14, 22, 48), stroke_width=3, stroke_fill="white")
    small = ImageFont.truetype(str(fonts.pick("A", "regular", "en")), 22)
    d.text((20, height - 32), ATTRIBUTION, font=small, fill=(110, 115, 140))
    if title:
        tf = ImageFont.truetype(str(fonts.pick(title, "bold", language)), 40)
        d.text((24, 16), title, font=tf, fill=(14, 22, 48))
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out
