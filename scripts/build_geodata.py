"""Builds the compact map dataset in assets/geo/ from authoritative open sources.

Sources (download into a folder and pass it as argv[1]):
  DataMeet India maps (CC BY 4.0) - India's official boundaries incl. J&K/Ladakh
    https://github.com/datameet/maps  States/Admin2.{shp,dbf,shx}
                                      Districts/Census_2011/2011_Dist.{shp,dbf,shx} -> Dist2011.*
  Natural Earth (public domain)      ne_10m_rivers_lake_centerlines.{shp,dbf,shx}
  GeoNames (CC BY 4.0)               IN.txt (from IN.zip), admin1CodesASCII.txt

Run:  python scripts/build_geodata.py /path/to/downloads
Needs: pip install pyshp
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import shapefile  # pyshp

SRC = Path(sys.argv[1])
OUT = Path(__file__).resolve().parent.parent / "assets" / "geo"
OUT.mkdir(parents=True, exist_ok=True)
INDIA_BBOX = (67.5, 6.0, 98.0, 37.5)


def _perp(p, a, b):
    (x, y), (x1, y1), (x2, y2) = p, a, b
    dx, dy = x2 - x1, y2 - y1
    if dx == dy == 0:
        return ((x - x1) ** 2 + (y - y1) ** 2) ** 0.5
    return abs(dy * x - dx * y + x2 * y1 - y2 * x1) / (dx * dx + dy * dy) ** 0.5


def simplify(pts, tol):
    """Iterative Douglas-Peucker."""
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        s, e = stack.pop()
        best, idx = 0.0, -1
        for i in range(s + 1, e):
            d = _perp(pts[i], pts[s], pts[e])
            if d > best:
                best, idx = d, i
        if best > tol and idx > 0:
            keep[idx] = True
            stack += [(s, idx), (idx, e)]
    return [p for p, k in zip(pts, keep) if k]


def rings(shape, tol, min_pts=4):
    parts = list(shape.parts) + [len(shape.points)]
    out = []
    for a, b in zip(parts, parts[1:]):
        ring = simplify([(round(x, 4), round(y, 4)) for x, y in shape.points[a:b]], tol)
        if len(ring) >= min_pts:
            out.append(ring)
    return out


def build_polygons(name, fields, tol, filt=lambda rec: True):
    r = shapefile.Reader(str(SRC / name))
    names = [f[0] for f in r.fields[1:]]
    feats = []
    for sr in r.iterShapeRecords():
        rec = dict(zip(names, sr.record))
        if not filt(rec):
            continue
        rs = rings(sr.shape, tol)
        if rs:
            feats.append({"props": {k: rec[k] for k in fields}, "rings": rs})
    return feats


def main():
    states = build_polygons("Admin2", ["ST_NM"], 0.012)
    states = [{"name": f["props"]["ST_NM"], "rings": f["rings"]} for f in states]
    districts = build_polygons("Dist2011", ["DISTRICT", "ST_NM"], 0.006)
    districts = [{"name": f["props"]["DISTRICT"], "state": f["props"]["ST_NM"], "rings": f["rings"]} for f in districts]

    r = shapefile.Reader(str(SRC / "ne_10m_rivers_lake_centerlines"))
    names = [f[0] for f in r.fields[1:]]
    rivers = []
    for sr in r.iterShapeRecords():
        rec = dict(zip(names, sr.record))
        b = sr.shape.bbox
        if not rec["name"] or b[2] < INDIA_BBOX[0] or b[0] > INDIA_BBOX[2] or b[3] < INDIA_BBOX[1] or b[1] > INDIA_BBOX[3]:
            continue
        lines = rings(sr.shape, 0.01, min_pts=2)
        if lines:
            rivers.append({"name": rec["name_en"] or rec["name"], "name_hi": rec.get("name_hi") or "", "lines": lines})

    admin1 = {}
    for line in (SRC / "admin1CodesASCII.txt").read_text(encoding="utf-8").splitlines():
        code, name, *_ = line.split("\t")
        if code.startswith("IN."):
            admin1[code[3:]] = name

    keep_any = {"PPLC", "PPLA", "PPLA2", "PRK", "RESW", "RESN", "NP", "FT", "PAL", "MNMT", "DAM", "LK", "LKS", "RSV", "MT", "PK", "MTS", "HLLS", "GRGE", "DSRT", "PLAT", "PASS", "CST", "BAY", "GULF", "ISL", "ISLS", "CAPE", "WTRF"}
    rajasthan_extra = {"TMPL", "HLL", "LKI", "RSVI", "RSVT", "STM", "ADM2", "ADM3", "CNL"}
    places = []
    for line in (SRC / "IN.txt").open(encoding="utf-8"):
        p = line.rstrip("\n").split("\t")
        fcode, a1, pop = p[7], p[10], int(p[14] or 0)
        ok = fcode in keep_any or (fcode.startswith("PPL") and pop >= 50000) or (a1 == "24" and (fcode in rajasthan_extra or (fcode.startswith("PPL") and pop >= 5000)))
        if not ok:
            continue
        hi = [n for n in p[3].split(",") if any("ऀ" <= ch <= "ॿ" for ch in n)][:2]
        places.append([p[1], p[2], round(float(p[4]), 4), round(float(p[5]), 4), fcode, admin1.get(a1, ""), pop, "|".join(hi)])

    def dump(obj, fname):
        (OUT / fname).write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"{fname}: {(OUT / fname).stat().st_size // 1024} KB")

    dump(states, "india_states.json")
    dump(districts, "india_districts_2011.json")
    dump(rivers, "rivers.json")
    dump({"columns": ["name", "ascii", "lat", "lon", "fcode", "state", "population", "names_hi"], "rows": places}, "places.json")


if __name__ == "__main__":
    main()
