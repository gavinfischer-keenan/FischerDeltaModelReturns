"""Which of a region's water the model covers: every mapped piece of open
water, intertidal flat and marsh in the region, and whether a reach carries
it (the Bay's mesh, a channel or river within its width, a pool's water), so
none is left out unseen. The bio-absorption layer to come will need the
flats and marshes as much as the open water.

The map is OpenStreetMap's (ref:osm), through the Overpass API, in the
region's extent (its published reaches, padded):
- open water: natural=water (lakes, ponds, lagoons, reservoirs, rivers'
  areas, canals' areas), waterway=riverbank, landuse=reservoir or basin;
- intertidal flats: natural=mud, natural=wetland with wetland=tidalflat;
- marsh: natural=wetland with wetland=saltmarsh, marsh, reedbed or swamp;
- salt ponds: landuse=salt_pond.

Each area is sampled at SAMPLES points inside it (an odd number of edge
crossings; a relation's member ways need not be joined into rings). Its area
is its box's times the share of the box's points inside. A sample is covered
if it lies in a mesh triangle, within a channel's half-width
(or MIN_HALF_WIDTH_M) plus LINE_SLACK_M of a reach's line, or within
POOL_SLACK_M of a pool's water point (or inside its disc). An area's coverage
is the share of its samples covered.

    python -m fdmr_runner.water_audit ../regions/sf-bay-delta <published cycle folder> ../docs/coverage/sf-bay-delta.md

It writes the report and, beside the region, uncovered_water.json: each
uncovered area's samples, for the map to show as water we do not model.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import fdmr_geo
import numpy as np

from fdmr_adapters import osm

SAMPLES = 60  # points inside each area: its coverage to within a few per cent
MIN_HALF_WIDTH_M = 30.0  # a channel or river with no published width counts as 60 m wide
LINE_SLACK_M = 30.0  # mapped banks and modelled lines differ by about this much
POOL_SLACK_M = 60.0  # a pool's water points are 60 m apart (pool_water.POINT_SPACING_M)
LISTED_M2 = 50_000.0  # areas of 5 ha or more are listed one by one; smaller ones are counted
UNCOVERED = 0.5  # an area with less than half its samples covered is listed as uncovered
M_PER_DEG = fdmr_geo.M_PER_DEG  # m per degree of latitude (fdmr_geo)
PAD_DEG = 0.02  # the extent beyond the published reaches

KINDS = {
    "water": ['way["natural"="water"]', 'relation["natural"="water"]', 'way["waterway"="riverbank"]',
              'relation["waterway"="riverbank"]', 'way["landuse"~"^(reservoir|basin)$"]', 'relation["landuse"~"^(reservoir|basin)$"]'],
    "flat": ['way["natural"="mud"]', 'relation["natural"="mud"]', 'way["natural"="wetland"]["wetland"="tidalflat"]',
             'relation["natural"="wetland"]["wetland"="tidalflat"]'],
    "marsh": ['way["natural"="wetland"]["wetland"~"^(saltmarsh|marsh|reedbed|swamp)$"]',
              'relation["natural"="wetland"]["wetland"~"^(saltmarsh|marsh|reedbed|swamp)$"]'],
    "salt_pond": ['way["landuse"="salt_pond"]', 'relation["landuse"="salt_pond"]'],
}
LABEL = {"water": "Open water", "flat": "Intertidal flats", "marsh": "Marsh", "salt_pond": "Salt ponds"}


def edges_of(el: dict) -> list[np.ndarray]:
    """An element's edges as arrays of (lon, lat) points."""
    if el.get("type") == "way" and el.get("geometry"):
        return [np.array([(g["lon"], g["lat"]) for g in el["geometry"]])]
    return [np.array([(g["lon"], g["lat"]) for g in m["geometry"]])
            for m in el.get("members", []) if m.get("type") == "way" and m.get("geometry")]


def inside(px: np.ndarray, py: np.ndarray, edges: list[np.ndarray]) -> np.ndarray:
    """Whether each point is inside the area the edges bound (odd crossings of a ray east)."""
    n = np.zeros(len(px), dtype=np.int64)
    for e in edges:
        if len(e) < 2:
            continue
        x0, y0, x1, y1 = e[:-1, 0], e[:-1, 1], e[1:, 0], e[1:, 1]
        for i in range(0, len(px), 256):
            X, Y = px[i:i + 256, None], py[i:i + 256, None]
            with np.errstate(divide="ignore", invalid="ignore"):
                cross = ((y0 > Y) != (y1 > Y)) & (X < x0 + (Y - y0) * (x1 - x0) / (y1 - y0))
            n[i:i + 256] += cross.sum(axis=1)
    return n % 2 == 1


def sample(edges: list[np.ndarray], rng: np.random.Generator, k: int = SAMPLES) -> tuple[np.ndarray, float]:
    """Up to k points inside the area, and its area, m2."""
    allp = np.concatenate(edges)
    w, s, e, n = allp[:, 0].min(), allp[:, 1].min(), allp[:, 0].max(), allp[:, 1].max()
    kx = math.cos(math.radians((s + n) / 2))
    box = (e - w) * kx * M_PER_DEG * (n - s) * M_PER_DEG
    tries = 8 * k
    px, py = rng.uniform(w, e, tries), rng.uniform(s, n, tries)
    ok = inside(px, py, edges)
    pts = np.column_stack([px[ok], py[ok]])[:k]
    return pts, box * ok.mean()


class Coverage:
    """The model's water, as places to test points against."""

    def __init__(self, cycle: Path) -> None:
        self.tris = []  # (lon, lat) of each mesh triangle's corners
        segs, half = [], []
        self.pool_pts, self.discs = [], []
        idx = json.loads((cycle / "index.json").read_text(encoding="utf-8"))
        self.extent = [math.inf, math.inf, -math.inf, -math.inf]
        for r in idx["reaches"]:
            f = cycle / "geometry" / f"{r['id']}.json"
            if not f.exists():
                continue
            g = json.loads(f.read_text(encoding="utf-8"))
            if g.get("dims") == 2 and "lon" in g:
                lon, lat = np.array(g["lon"]), np.array(g["lat"])
                t = np.array(g["triangles"]).reshape(-1, 3)
                self.tris.append(np.stack([np.column_stack([lon[t[:, i]], lat[t[:, i]]]) for i in range(3)], axis=1))
                self._grow(lon, lat)
                continue
            for i, line in enumerate(g.get("lines", [])):
                a = np.array(line, dtype=float)
                if len(a) < 2:
                    continue
                w = (g.get("elements") or [{}] * len(g["lines"]))[i].get("width_m") if g.get("elements") else None
                hw = max(MIN_HALF_WIDTH_M, (w or 0) / 2) + LINE_SLACK_M
                segs.append(np.column_stack([a[:-1], a[1:]]))
                half.append(np.full(len(a) - 1, hw))
                self._grow(a[:, 0], a[:, 1])
            at = {n["id"]: (n["lon"], n["lat"]) for n in g.get("nodes", []) if "lon" in n}
            for p in g.get("pools", []):
                if p.get("points"):
                    self.pool_pts.append(np.array(p["points"], dtype=float))
                else:
                    if "lon" in p:
                        c = (p["lon"], p["lat"])
                    else:
                        q = [at[n] for n in p.get("nodes", []) if n in at]
                        c = (sum(x for x, _ in q) / len(q), sum(y for _, y in q) / len(q)) if q else None
                    if c:
                        self.discs.append((c[0], c[1], math.sqrt(p["area_m2"] / math.pi)))
        self.segs = np.concatenate(segs) if segs else np.zeros((0, 4))
        self.half = np.concatenate(half) if half else np.zeros(0)
        self.tri = np.concatenate(self.tris) if self.tris else np.zeros((0, 3, 2))
        self.pool = np.concatenate(self.pool_pts) if self.pool_pts else np.zeros((0, 2))

    def _grow(self, lon: np.ndarray, lat: np.ndarray) -> None:
        self.extent = [min(self.extent[0], float(np.min(lon))), min(self.extent[1], float(np.min(lat))),
                       max(self.extent[2], float(np.max(lon))), max(self.extent[3], float(np.max(lat)))]

    def bbox(self) -> tuple[float, float, float, float]:
        w, s, e, n = self.extent
        return w - PAD_DEG, s - PAD_DEG, e + PAD_DEG, n + PAD_DEG

    def covered(self, pts: np.ndarray) -> np.ndarray:
        """Whether each (lon, lat) point lies in the model's water."""
        out = np.zeros(len(pts), dtype=bool)
        for i, (x, y) in enumerate(pts):
            kx = math.cos(math.radians(y)) * M_PER_DEG
            ky = M_PER_DEG
            # In a mesh triangle (barycentric weights all non-negative).
            if len(self.tri):
                t = self.tri
                near = (t[:, :, 0].min(1) <= x) & (t[:, :, 0].max(1) >= x) & (t[:, :, 1].min(1) <= y) & (t[:, :, 1].max(1) >= y)
                for a, b, c in t[near]:
                    d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
                    if d == 0:
                        continue
                    l1 = ((b[1] - c[1]) * (x - c[0]) + (c[0] - b[0]) * (y - c[1])) / d
                    l2 = ((c[1] - a[1]) * (x - c[0]) + (a[0] - c[0]) * (y - c[1])) / d
                    if l1 >= -1e-9 and l2 >= -1e-9 and l1 + l2 <= 1 + 1e-9:
                        out[i] = True
                        break
                if out[i]:
                    continue
            # Within a line's half-width.
            if len(self.segs):
                s = self.segs
                ax, ay, bx, by = (s[:, 0] - x) * kx, (s[:, 1] - y) * ky, (s[:, 2] - x) * kx, (s[:, 3] - y) * ky
                dx, dy = bx - ax, by - ay
                L2 = dx * dx + dy * dy
                with np.errstate(divide="ignore", invalid="ignore"):
                    f = np.clip(np.where(L2 > 0, -(ax * dx + ay * dy) / L2, 0.0), 0, 1)
                dist = np.hypot(ax + f * dx, ay + f * dy)
                if np.any(dist <= self.half):
                    out[i] = True
                    continue
            # On a pool's water.
            if len(self.pool) and np.min(np.hypot((self.pool[:, 0] - x) * kx, (self.pool[:, 1] - y) * ky)) <= POOL_SLACK_M:
                out[i] = True
                continue
            if any(math.hypot((cx - x) * kx, (cy - y) * ky) <= r for cx, cy, r in self.discs):
                out[i] = True
        return out


def audit(cycle: Path, get=osm.query, seed: int = 2026) -> dict:
    cov = Coverage(cycle)
    w, s, e, n = cov.bbox()
    rng = np.random.default_rng(seed)
    kinds: dict[str, dict] = {}
    areas: list[dict] = []
    for kind, selectors in KINDS.items():
        q = "".join(f"{sel}({s},{w},{n},{e});" for sel in selectors)
        d = get(f"[out:json][timeout:300];({q});out geom;")
        tot = {"count": 0, "area_m2": 0.0, "covered_m2": 0.0, "small_count": 0, "small_area_m2": 0.0, "small_uncovered_m2": 0.0}
        for el in d.get("elements", []):
            edges = edges_of(el)
            if not edges or sum(len(x) for x in edges) < 4:
                continue
            pts, area = sample(edges, rng)
            if not len(pts) or area <= 0:
                continue
            share = float(cov.covered(pts).mean())
            tot["count"] += 1
            tot["area_m2"] += area
            tot["covered_m2"] += area * share
            tags = el.get("tags", {})
            rec = {"osm": f"{el['type']}/{el['id']}", "kind": kind, "name": tags.get("name"),
                   "tag": tags.get("water") or tags.get("wetland") or tags.get("landuse") or tags.get("natural") or tags.get("waterway"),
                   "area_m2": round(area), "covered": round(share, 2),
                   "lon": round(float(pts[:, 0].mean()), 5), "lat": round(float(pts[:, 1].mean()), 5)}
            if area < LISTED_M2:
                tot["small_count"] += 1
                tot["small_area_m2"] += area
                tot["small_uncovered_m2"] += area * (1 - share)
            if share < UNCOVERED:
                rec["samples"] = [[round(float(x), 5), round(float(y), 5)] for x, y in pts]
                areas.append(rec)
        kinds[kind] = {k: round(v) if isinstance(v, float) else v for k, v in tot.items()}
    areas.sort(key=lambda r: -r["area_m2"] * (1 - r["covered"]))
    return {"built": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "cycle": cycle.name, "bbox": [w, s, e, n],
            "kinds": kinds, "uncovered": areas, "attribution": osm.ATTRIBUTION}


def report(a: dict, region: str) -> str:
    km2 = lambda m2: f"{m2 / 1e6:,.2f}"  # noqa: E731
    out = [f"# Water the model covers: {region}", "",
           f"Built {a['built']} by `fdmr_runner.water_audit` from the published cycle {a['cycle']} and OpenStreetMap",
           f"(ref:osm; {a['attribution']}), in the box {', '.join(f'{x:.3f}' for x in a['bbox'])} (west, south, east, north).",
           "How each area is tested is in the module's docstring.", "",
           "| Kind | Areas | Mapped, km² | Covered by a reach, km² | Not covered, km² | Of which in areas under 5 ha |",
           "|---|---|---|---|---|---|"]
    for k, t in a["kinds"].items():
        out.append(f"| {LABEL[k]} | {t['count']} | {km2(t['area_m2'])} | {km2(t['covered_m2'])} | "
                   f"{km2(t['area_m2'] - t['covered_m2'])} | {t['small_count']} areas, {km2(t['small_uncovered_m2'])} km² |")
    big = [r for r in a["uncovered"] if r["area_m2"] >= LISTED_M2]
    out += ["", f"## Uncovered areas of 5 ha or more ({len(big)}), largest uncovered first", "",
            "Covered under half. Each links to OpenStreetMap.", "",
            "| Kind | Name | Tag | Area, km² | Covered | Where (lon, lat) | OSM |", "|---|---|---|---|---|---|---|"]
    for r in big:
        url = f"https://www.openstreetmap.org/{r['osm']}"
        out.append(f"| {LABEL[r['kind']]} | {r['name'] or ''} | {r['tag'] or ''} | {km2(r['area_m2'])} | {r['covered']:.0%} | "
                   f"{r['lon']}, {r['lat']} | [{r['osm']}]({url}) |")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("region")
    ap.add_argument("cycle", help="a published cycle folder (holding index.json and geometry/)")
    ap.add_argument("out", help="the report, Markdown")
    args = ap.parse_args(argv)
    region_dir = Path(args.region)
    a = audit(Path(args.cycle))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(report(a, region_dir.name), encoding="utf-8")
    (region_dir / "uncovered_water.json").write_text(json.dumps(a, separators=(",", ":")) + "\n", encoding="utf-8")
    for k, t in a["kinds"].items():
        print(f"{LABEL[k]}: {t['count']} areas, {t['area_m2'] / 1e6:.1f} km2, {t['covered_m2'] / 1e6:.1f} covered")
    print(f"uncovered areas: {len(a['uncovered'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
