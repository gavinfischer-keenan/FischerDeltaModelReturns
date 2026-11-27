"""The nightly regime (issue #9): look back at how the predictions did, run a
hindcast, and keep a log for a person to read. Nothing here changes the model:
tuning is done by hand, from these logs, and never silently.

    python -m fdmr_runner.nightly REGION_DIR STORE_FILE DATA_ROOT [--hindcast-days 5]

Each night (00:00 Pacific Standard Time, runner/schedule):

1. A hindcast as of `--hindcast-days` days ago (00:00 PST): the Delta (DSM2,
   `fdmr_runner.delta --as-of`) and every river (`fdmr_runner.publish --as-of`),
   using only what was measured before that time; river forecasts are the
   River Forecast Center's issue in force then where it was kept (from
   26 Nov 2026), and every other forecast input is held. The Bay and the canals are not
   hindcast yet. Its predictions go into the prediction ledger as `hindcast`.
2. The ledger scored, live and hindcast apart, each against persistence.
3. The night's log, DATA_ROOT/publish/<region>/nightly/<date>.json, and the
   list of logs, nightly/index.json, served beside the cycles (nightly.html).

Data measured before the hindcast's time but fetched or revised after it is
used as it stands now (the store keeps the latest version of each value).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from fdmr_store import Store

from . import ledger
from .delta import pst_midnight
from .region import load_region

DAY = 86_400
KEEP_LOGS = 120  # nightly review logs kept: the last 120 nights
TAIL_LINES = 60  # each step's last lines of output kept in the log: enough for every check's result (FDMR's choice)


def run(cmd: list[str], log: list[dict], what: str) -> bool:
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True)
    tail = (p.stdout + p.stderr).strip().splitlines()[-TAIL_LINES:]
    log.append({"step": what, "ok": p.returncode == 0, "seconds": round(time.time() - t0), "tail": tail})
    print(f"{what}: {'ok' if p.returncode == 0 else 'FAILED'} in {time.time() - t0:.0f} s", flush=True)
    return p.returncode == 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("region")
    ap.add_argument("store")
    ap.add_argument("data")
    ap.add_argument("--hindcast-days", type=int, default=5)
    args = ap.parse_args(argv)
    region_dir = Path(args.region).resolve()
    region = load_region(region_dir)
    data = Path(args.data)
    now = int(time.time())
    as_of = pst_midnight(now) - args.hindcast_days * DAY
    as_of_iso = datetime.fromtimestamp(as_of, timezone.utc).strftime("%Y-%m-%dT%H:%M")
    hd = data / "hindcast" / region.id
    py = sys.executable
    steps: list[dict] = []

    delta_pub = hd / "delta" / "published"
    have_delta = (region_dir / "dsm2.toml").exists()
    if have_delta:
        (hd / "delta" / "runs").mkdir(parents=True, exist_ok=True)
        delta_pub.mkdir(parents=True, exist_ok=True)
        have_delta = run([py, "-m", "fdmr_runner.delta", str(region_dir), args.store, str(hd / "delta" / "runs"),
                          "--as-of", as_of_iso, "--publish", str(delta_pub)], steps, "delta hindcast")
    pub = hd / "publish"
    cmd = [py, "-m", "fdmr_runner.publish", str(region_dir), args.store, str(pub), "--as-of", as_of_iso, "--keep", "2"]
    cal = data / "calibration" / f"{region.id}.json"
    if cal.exists():
        cmd += ["--calibration", str(cal)]
    if have_delta and (delta_pub / "latest").exists():
        cmd += ["--with-reach", str(delta_pub / "latest")]
    hindcast = None
    if run(cmd, steps, "rivers hindcast and publish"):
        cdir = (pub / "current").resolve()
        index = json.loads((cdir / "index.json").read_text(encoding="utf-8"))
        store = Store(args.store)
        db = ledger.connect(Path(args.store).parent / "ledger" / f"{region.id}.sqlite")
        kept = ledger.record(db, region_dir, cdir, index, store, kind="hindcast")
        hindcast = {"as_of": index["now"], "cycle": index["cycle"], "kept": kept, "reaches": [r["id"] for r in index["reaches"]],
                    "forecasts": index.get("hindcast", {}).get("forecasts", ""), "not_hindcast": ["the Bay", "the export canals"]}
        db.close()
    store = Store(args.store)
    db = ledger.connect(Path(args.store).parent / "ledger" / f"{region.id}.sqlite")
    score = ledger.score(db, store, now)
    db.close()
    live = data / "publish" / region.id / "current" / "index.json"
    version = json.loads(live.read_text(encoding="utf-8")).get("model_version") if live.exists() else None
    day = datetime.fromtimestamp(pst_midnight(now), timezone.utc).strftime("%Y-%m-%d")
    logs = data / "publish" / region.id / "nightly"
    logs.mkdir(parents=True, exist_ok=True)
    entry = {"date": day, "generated": now, "model_version": version, "hindcast": hindcast, "steps": steps, "score": score}
    (logs / f"{day}.json").write_text(json.dumps(entry, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    dates = sorted((p.stem for p in logs.glob("????-??-??.json")), reverse=True)
    for old in dates[KEEP_LOGS:]:
        (logs / f"{old}.json").unlink()
    (logs / "index.json").write_text(json.dumps({"logs": dates[:KEEP_LOGS]}), encoding="utf-8")
    print(f"nightly log {logs / (day + '.json')}: hindcast {'kept ' + str(hindcast['kept']) if hindcast else 'FAILED'}", flush=True)
    return 0 if hindcast else 1


if __name__ == "__main__":
    sys.exit(main())
