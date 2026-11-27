"""Cut a region's basemap extract from the latest Protomaps build (specification section 5.1).

    python -m fdmr_runner.basemap REGION_DIR OUT_DIR [--pmtiles PATH]

Reads [basemap] bbox and maxzoom from the region package and writes
OUT_DIR/<region>.pmtiles, downloading only the tiles inside the box. Needs
the `pmtiles` command-line tool (github.com/protomaps/go-pmtiles).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

from .region import load_region

BUILDS = "https://build-metadata.protomaps.dev/builds.json"
BUILD_BASE = "https://build.protomaps.com/"


def latest_build() -> str:
    req = urllib.request.Request(BUILDS, headers={"User-Agent": "fdmr-runner/0.1"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return BUILD_BASE + json.load(r)[-1]["key"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("region")
    ap.add_argument("out")
    ap.add_argument("--pmtiles", default="pmtiles", help="path to the pmtiles tool")
    args = ap.parse_args(argv)
    region = Path(args.region)
    r = load_region(region)
    rid, bm = r.id, r.basemap
    w, s, e, n = bm["bbox"]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tmp, final = out / f"{rid}.pmtiles.tmp", out / f"{rid}.pmtiles"
    src = latest_build()
    print(f"extracting {rid} from {src}", flush=True)
    subprocess.run(
        [args.pmtiles, "extract", src, str(tmp), f"--bbox={w},{s},{e},{n}", f"--maxzoom={bm.get('maxzoom', 15)}"],
        check=True,
    )
    tmp.replace(final)  # swap in one step, so the map never reads half a file
    print(f"{final}: {final.stat().st_size / 1e6:.0f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
