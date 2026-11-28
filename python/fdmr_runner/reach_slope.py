"""A river reach's bed slope from NHDPlus Version 2 (ref:nhdplus): the
length-weighted mean of the slopes of the main-stem flowlines from the
reach's top down its length (NLDI's downstream navigation, ref:nldi). A reach
built from named NHD flowlines (a floodway's channel, `line.source = "nhd"`)
takes the NHDPlus flowlines of those names found under points every
SNAP_EVERY_M along its line. It is the slope of a reach with fewer than two
main gauges, and every reach's when its gauges give none (#79). It prints a
line to put in the reach's entry in region.toml.

    python -m fdmr_runner.reach_slope ../regions/sf-bay-delta american bear
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tomllib
from datetime import date
from pathlib import Path

from fdmr_adapters import nhdplus
from fdmr_adapters.nldi import NldiAdapter

SNAP_EVERY_M = 1500.0  # a point every 1.5 km along a named-flowline reach: NHDPlus flowlines are a few km long


def weighted(values: dict[int, tuple[float, float]]) -> tuple[float, float]:
    """The length-weighted mean slope and the length it covers, km."""
    total = sum(length for _, length in values.values())
    if total <= 0:
        raise ValueError("no flowline with a length")
    return sum(s * length for s, length in values.values()) / total, total


def reach_slope(region_dir: Path, reach: str, nldi: NldiAdapter | None = None, get=nhdplus.get_json) -> dict:
    geo = json.loads((region_dir / f"{reach}.json").read_text(encoding="utf-8"))
    cfg = next(r for r in tomllib.loads((region_dir / "region.toml").read_text(encoding="utf-8"))["reaches"] if r["id"] == reach)
    lon, lat = geo["coords"][0][:2]
    nldi = nldi or NldiAdapter()
    top = nldi.point_position(lon, lat, reach)
    length_km = geo.get("model_length_m", geo["length_m"]) / 1000
    line = cfg.get("line", {})
    if line.get("source") == "nhd":
        # The flowlines of the reach's names under points along its line.
        comids, run, last = set(), 0.0, None
        for x, y, *_ in geo["coords"]:
            if last is not None:
                run += math.hypot((x - last[0]) * 111_195 * math.cos(math.radians(y)), (y - last[1]) * 111_195)
            if last is None or run >= SNAP_EVERY_M:
                comids.add(nldi.point_position(x, y, reach).comid)
                run = 0.0
            last = (x, y)
        names: dict[int, str] = {}
        got = nhdplus.slopes(sorted(comids), get=get, names=names)
        got = {c: v for c, v in got.items() if names.get(c) in set(line["names"])}
    else:
        flowlines = nldi.downstream_from(top.comid, distance_km=length_km)
        got = nhdplus.slopes([f.comid for f in flowlines], get=get)
    slope, covered = weighted(got)
    return {"reach": reach, "bed_slope": slope, "flowlines": len(got), "km": covered, "top_comid": top.comid,
            "length_km": length_km}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("region")
    ap.add_argument("reaches", nargs="+")
    args = ap.parse_args(argv)
    for r in args.reaches:
        s = reach_slope(Path(args.region), r)
        print(f"{r}: bed_slope = {s['bed_slope']:.2e}   # NHDPlus V2 slope, length-weighted over {s['flowlines']} flowlines, "
              f"{s['km']:.1f} km down from COMID {s['top_comid']} (reach {s['length_km']:.1f} km; ref:nhdplus), read {date.today()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
