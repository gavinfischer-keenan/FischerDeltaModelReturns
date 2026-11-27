"""A trial (issue #26): the rivers run as a hindcast with other registered
maths in place of the region's, labelled, and scored apart from the live
model and its nightly hindcasts.

    python -m fdmr_runner.trial REGION_DIR STORE_FILE DATA_ROOT --name NAME \\
        --maths REACH.PART=MATHS [--maths ...] [--as-of 2026-11-22T08:00] [--hindcast-days 5]

`--maths` picks, for a reach (or `*` for every reach), another registered
implementation of a part (`fdmr_plugins.registry`; `docs/maths.md`): for
instance `*.dispersion=fischer-longitudinal`. Only registered maths can run:
a candidate the register lists without code (registry.CANDIDATES) cannot,
and an unknown choice stops the trial.

The cycle is published to DATA_ROOT/trials/<region>/<name>/, its index.json
saying `trial` (the name and the maths changed), and its predictions are kept
in a ledger of its own, DATA_ROOT/store/ledger/<region>.trial-<name>.sqlite,
never mixed with the live model's. Its score, by the ledger's own method
(each prediction against what was measured, and against persistence), is
written beside it as score.json, to read against the nightly hindcast's.
Adopting the maths it tried stays a change made by hand, with its source.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from fdmr_store import Store

from . import ledger, publish
from .delta import pst_midnight
from .region import load_region

DAY = 86_400
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


def parse_maths(items: list[str]) -> dict[str, dict[str, str]]:
    """`REACH.PART=MATHS` items as {reach: {part: maths}}."""
    out: dict[str, dict[str, str]] = {}
    for item in items:
        m = re.fullmatch(r"([*\w-]+)\.([\w-]+)=([\w-]+)", item)
        if not m:
            raise ValueError(f"--maths {item!r}: expected REACH.PART=MATHS")
        out.setdefault(m.group(1), {})[m.group(2)] = m.group(3)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("region")
    ap.add_argument("store")
    ap.add_argument("data")
    ap.add_argument("--name", required=True, help="the trial's name: lower case, digits and hyphens")
    ap.add_argument("--maths", action="append", required=True, help="REACH.PART=MATHS, repeatable; REACH may be *")
    ap.add_argument("--as-of", help="the hindcast's time (UTC); default: --hindcast-days before 00:00 PST today")
    ap.add_argument("--hindcast-days", type=int, default=5)
    args = ap.parse_args(argv)
    if not NAME.match(args.name):
        raise SystemExit("--name: lower case letters, digits and hyphens")
    region_dir, data = Path(args.region), Path(args.data)
    region = load_region(region_dir)
    trial = {"name": args.name, "maths": parse_maths(args.maths)}
    as_of = (int(datetime.fromisoformat(args.as_of).replace(tzinfo=timezone.utc).timestamp()) if args.as_of
             else pst_midnight(int(time.time())) - args.hindcast_days * DAY)
    out = data / "trials" / region.id / args.name
    out.mkdir(parents=True, exist_ok=True)
    cal = data / "calibration" / f"{region.id}.json"
    calibration = json.loads(cal.read_text(encoding="utf-8")) if cal.exists() else None
    store = Store(args.store)
    # The same gauge datums as the publisher's own runs (publish.main), for the rivers' surface slopes.
    datums = publish.held_datums(region, store)
    cdir = publish.publish(region_dir, store, out, 14, datums=datums, calibration=calibration, as_of=as_of, trial=trial)
    publish.prune(out, 2)
    index = json.loads((cdir / "index.json").read_text(encoding="utf-8"))
    db = ledger.connect(Path(args.store).parent / "ledger" / f"{region.id}.trial-{args.name}.sqlite")
    kept = ledger.record(db, region_dir, cdir, index, store, kind=f"trial:{args.name}")
    score = ledger.score(db, store, int(time.time()))
    db.close()
    (out / "score.json").write_text(json.dumps({"trial": index.get("trial"), "cycle": index["cycle"], "kept": kept, "score": score},
                                               separators=(",", ":")), encoding="utf-8")
    print(f"trial {args.name}: {cdir} ({kept} predictions kept; score in {out / 'score.json'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
