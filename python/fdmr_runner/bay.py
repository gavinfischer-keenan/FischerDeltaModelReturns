"""Fetch NOAA's Bay forecast and publish it as the Bay reach (specification 7.3).

    python -m fdmr_runner.bay REGION_DIR WORK_DIR [--publish OUT_DIR] [--days 14]

Reads REGION_DIR/bay.toml. The first run downloads one whole field file, reads
from it where each variable lies (fdmr_plugins.fvcom.layout) and builds the
coarse mesh (fdmr_plugins.fvcom.mesh), kept in WORK_DIR. Every run then makes
sure WORK_DIR holds each hour from `--days` back to the end of the latest
cycle's forecast, each fetched once as a byte range and kept as its fields on
the coarse mesh: the past from nowcasts, which do not change, and the forecast
from the latest cycle, replaced each cycle. With `--publish`, the hours become
a reach in OUT_DIR/<stamp>, with OUT_DIR/latest pointing at it, for the
region's publish step to take in (`--with-reach`).

Past NOAA's forecast (48 hours) nothing is published yet: the extension that
carries the Bay to 14 days is a later step of Phase 3.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from fdmr_adapters import noaa_ofs
from fdmr_plugins.fvcom import fields, mesh
from fdmr_plugins.fvcom.layout import Layout, read_layout

HOUR = 3600
DAY = 86_400
# Raised whenever the coarsening changes, so meshes and hours made the old way are made again.
MESH_VERSION = 2


def stamp(t: int) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y%m%dT%H%MZ")


def load(region_dir: Path) -> dict | None:
    p = region_dir / "bay.toml"
    return tomllib.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def prepare(cfg: dict, work: Path, cycle: int) -> tuple[Layout, mesh.Coarse, dict]:
    """The file layout and coarse mesh, made once (again if the mesh settings change)."""
    work.mkdir(parents=True, exist_ok=True)
    template = work / "template.nc"
    if not template.exists():
        noaa_ofs.download(noaa_ofs.file_url(cfg["ofs"], cycle, "f", 0), template)
    lay_file = work / "layout.json"
    if not lay_file.exists():
        lay_file.write_text(read_layout(template).to_json(), encoding="utf-8")
    layout = Layout.from_json(lay_file.read_text(encoding="utf-8"))
    settings = json.dumps({"mesh": cfg["mesh"], "version": MESH_VERSION}, sort_keys=True)
    mesh_file, made_with = work / "mesh.npz", work / "mesh.settings.json"
    if mesh_file.exists() and made_with.exists() and made_with.read_text(encoding="utf-8") == settings:
        coarse = mesh.Coarse.load(mesh_file)
    else:
        m = cfg["mesh"]
        fine = mesh.read_fine(template)
        keep = np.all((fine.lon[fine.tri] < m["east_of"]) & (fine.lon[fine.tri] > m["west_of"]), axis=1)
        coarse = mesh.coarsen(fine, keep, m["k"], m["min_m"], m["max_m"])
        coarse.save(mesh_file)
        made_with.write_text(settings, encoding="utf-8")
        shutil.rmtree(work / "hours", ignore_errors=True)  # hours on the old mesh
        shutil.rmtree(work / "forecast", ignore_errors=True)
    edges = mesh.classify(coarse, cfg["mesh"]["open_sea"], cfg["mesh"].get("handoffs", []))
    return layout, coarse, edges


def fetch_hour(cfg: dict, layout: Layout, coarse: mesh.Coarse, t: int, source: tuple[int, str, int], path: Path) -> str:
    url = noaa_ofs.file_url(cfg["ofs"], *source)
    lo, hi = layout.span()
    when, f = fields.hour(layout, noaa_ofs.fetch_range(url, lo, hi), lo, coarse)
    if when != t:
        raise ValueError(f"{url} holds {stamp(when)}, not {stamp(t)}: the file layout may have changed")
    tmp = path.with_suffix(".tmp.npz")
    np.savez(tmp, **f)
    tmp.replace(path)
    return url.rsplit("/", 1)[1]


def gather(cfg: dict, work: Path, layout: Layout, coarse: mesh.Coarse, now: int, latest: int, days: int) -> dict[int, Path]:
    """Every hour from `days` back to the end of the latest forecast, fetched if missing."""
    hours_dir, fc_dir = work / "hours", work / "forecast" / stamp(latest)
    hours_dir.mkdir(parents=True, exist_ok=True)
    fc_dir.mkdir(parents=True, exist_ok=True)
    start = (now - days * DAY) // HOUR * HOUR
    want: dict[int, Path] = {}
    todo = []
    for t in range(start, latest + cfg["forecast_hours"] * HOUR + 1, HOUR):
        path = (hours_dir if t <= latest else fc_dir) / f"{stamp(t)}.npz"
        want[t] = path
        if not path.exists():
            todo.append((t, noaa_ofs.source_of(t, cfg["cycles"], latest), path))
    failed = 0
    with ThreadPoolExecutor(4) as pool:
        futures = {pool.submit(fetch_hour, cfg, layout, coarse, t, src, p): t for t, src, p in todo}
        for fut, t in futures.items():
            try:
                fut.result()
            except Exception as e:  # one missing hour must not stop the rest
                failed += 1
                print(f"{stamp(t)} FAILED {e}", flush=True)
    print(f"hours: {len(want)} wanted, {len(todo) - failed} fetched, {failed} failed", flush=True)
    # Older forecasts and hours long past are not needed again.
    for old in (work / "forecast").iterdir():
        if old != fc_dir:
            shutil.rmtree(old, ignore_errors=True)
    for p in hours_dir.glob("*.npz"):
        if p.stem < stamp(start - 2 * DAY):
            p.unlink()
    return {t: p for t, p in want.items() if p.exists()}


def publish(cfg: dict, region_dir: Path, coarse: mesh.Coarse, edges: dict, hours: dict[int, Path], now: int, latest: int, out_root: Path, keep: int = 3) -> Path:
    import fdmr_py

    from .region import load_region

    reach = cfg["reach"]
    pub = out_root / stamp(latest)
    if pub.exists():
        shutil.rmtree(pub)  # never rewrite a published file in place
    (pub / "geometry").mkdir(parents=True)
    (pub / "geometry" / f"{reach}.json").write_text(json.dumps(mesh.geometry(coarse, reach, edges), separators=(",", ":"), allow_nan=False), encoding="utf-8")
    data = {t: dict(np.load(p)) for t, p in sorted(hours.items())}
    start, end = min(data), max(data) + HOUR
    days = fields.write_days(data, reach, pub / reach, start, end)
    rc = next((r for r in load_region(region_dir).reaches if r["id"] == reach), {})
    cert = rc.get("certainty", {})
    solid, floor = cert.get("solid_days", 2) * DAY, cert.get("floor", 0.2)
    utc = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%MZ")  # noqa: E731
    entry = {
        "id": reach, "kind": rc.get("kind", "2d-field"), "dims": 2, "elements": len(coarse.x), "layers": 1,
        "fields": list(fields.FIELDS), "quality_fields": list(fields.QUALITY),
        "time": {"start": utc(start), "step_s": HOUR, "days": days},
        "now": utc(now // HOUR * HOUR),
        "certainty": [round(fdmr_py.certainty_at(t - now, solid, end - now, floor), 4) for t in range(start, end, HOUR)],
        "flow_source": f"{rc.get('flow_source', 'noaa-ofs')} cycle {stamp(latest)}",
    }
    (pub / "reach.json").write_text(json.dumps(entry, separators=(",", ":")), encoding="utf-8")
    tmp = out_root / ".latest.tmp"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(pub.name)
    tmp.replace(out_root / "latest")
    olds = sorted(p for p in out_root.iterdir() if p.is_dir() and not p.is_symlink() and p.name != pub.name)
    for p in olds[: max(0, len(olds) - (keep - 1))]:
        shutil.rmtree(p)
    return pub


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("region")
    ap.add_argument("work")
    ap.add_argument("--publish", help="publish the reach into this folder (its `latest` link)")
    ap.add_argument("--days", type=int, help="days of the past (default: days_back in bay.toml)")
    args = ap.parse_args(argv)
    region_dir, work = Path(args.region), Path(args.work)
    cfg = load(region_dir)
    if cfg is None:
        print("no bay.toml in this region")
        return 0
    now = int(time.time())
    latest = noaa_ofs.latest_cycle(cfg["ofs"], cfg["cycles"], now, cfg["forecast_hours"])
    if latest is None:
        print("no complete NOAA cycle in the last day")
        return 1
    print(f"latest cycle {stamp(latest)}", flush=True)
    layout, coarse, edges = prepare(cfg, work, latest)
    print(f"mesh: {len(coarse.x)} nodes, {len(coarse.tri)} triangles; edges: "
          + ", ".join(f"{k} {len(v)}" for k, v in edges.items()), flush=True)
    t = time.time()
    hours = gather(cfg, work, layout, coarse, now, latest, args.days or cfg.get("days_back", 14))
    print(f"gathered in {time.time() - t:.0f} s", flush=True)
    if args.publish:
        pub = publish(cfg, region_dir, coarse, edges, hours, now, latest, Path(args.publish))
        size = sum(p.stat().st_size for p in pub.rglob("*") if p.is_file())
        print(f"published {pub} ({size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
