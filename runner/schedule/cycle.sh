#!/bin/sh
# One model-runner cycle for every region (specification 9.3, cadence in the
# decisions log). Run by systemd timers as the fdmr user.
#
#   cycle.sh hourly    gauges and forecasts, then publish
#   cycle.sh nightly   the same, plus channel measurements, recalibration and the
#                      nightly regime (fdmr_runner.nightly: hindcast, scores, log)
#   cycle.sh delta     the same as hourly, plus a DSM2 run for every region that
#                      has one (regions/<id>/dsm2.toml) and NOAA's latest Bay
#                      forecast for every region with a bay (regions/<id>/bay.toml),
#                      every 6 hours
#
# Every publish includes the region's latest DSM2 run, its export canals and
# its Bay forecast, as reaches of their own. A region's failure does not stop
# the next one. A cycle still running when the next is due makes the next one
# wait its turn (flock), for at most FDMR_LOCK_WAIT seconds, well inside the
# unit's 50-minute timeout; past that it stops, recorded as failed (#86).
#
# What ran is recorded (#55): each region's steps before its publish, with
# whether each worked, how long it took and its peak memory (#36), in
# $DATA/store/cycle-steps-<region>.json
# (publish copies them into the cycle's index.json and reports a failed one);
# and each mode's last run, in $DATA/publish/cycle-runs.json, served beside the
# cycles for check.html.
set -u
MODE=${1:-hourly}
ROOT=${FDMR_ROOT:-$HOME/fdmr}
DATA=${FDMR_DATA:-/srv/fdmr-data}
PY=${FDMR_PYTHON:-$HOME/venv/bin/python}
REGIONS=${FDMR_REGIONS:-"sf-bay-delta test-colorado rhine-meuse"}

WAIT=${FDMR_LOCK_WAIT:-1500}  # 25 min: inside the unit's 50 min (fdmr-cycle@.service)
exec 9>"$DATA/.cycle.lock"
asked=$(date +%s)
if ! flock -w "$WAIT" 9; then
  echo "$MODE: waited $WAIT s for the cycle lock: not run" >&2
  exit 75
fi
echo "$MODE: waited $(( $(date +%s) - asked )) s for the cycle lock"

case "$MODE" in
  hourly|delta) FETCH="--days 16 --no-measurements" ;;
  nightly) FETCH="--days 16" ;;
  *) echo "unknown mode: $MODE" >&2; exit 2 ;;
esac

cd "$ROOT/python" || exit 1
status=0
started=$(date +%s)

# step NAME COMMAND...: run one step, and record whether it worked, how long
# it took and the most memory any one of its processes needed (fdmr_runner.stepstat).
step() {
  name=$1; shift
  "$PY" -m fdmr_runner.stepstat "$steps.tmp" "$name" -- "$@" || status=1
}

for region in $REGIONS; do
  steps="$DATA/store/cycle-steps-$region.json"
  printf '[' > "$steps.tmp"
  rdir="$ROOT/regions/$region"
  store="$DATA/store/fdmr.sqlite"
  echo "== $region: fetch ($MODE)"
  # shellcheck disable=SC2086
  step fetch "$PY" -m fdmr_runner.fetch "$rdir" "$store" $FETCH --pause 1
  if [ "$MODE" = nightly ]; then
    echo "== $region: calibrate"
    step calibrate "$PY" -m fdmr_runner.calibrate "$rdir" "$store" "$DATA/calibration"
  fi
  delta="$DATA/delta/$region"
  if [ "$MODE" = delta ] && [ -f "$rdir/dsm2.toml" ]; then
    echo "== $region: DSM2"
    mkdir -p "$delta/runs" "$delta/published"
    step dsm2 "$PY" -m fdmr_runner.delta "$rdir" "$store" "$delta/runs" --publish "$delta/published"
  fi
  canals="$DATA/canals/$region"
  if [ "$MODE" = delta ] && [ -f "$rdir/canals.toml" ] && [ -e "$delta/published/latest" ]; then
    echo "== $region: canals"
    mkdir -p "$canals/published"
    step canals "$PY" -m fdmr_runner.canals publish "$rdir" "$delta/published/latest" "$canals/published"
  fi
  bay="$DATA/bay/$region"
  if [ "$MODE" = delta ] && [ -f "$rdir/bay.toml" ]; then
    echo "== $region: Bay (NOAA)"
    mkdir -p "$bay/published"
    step bay "$PY" -m fdmr_runner.bay "$rdir" "$bay" --publish "$bay/published" --archive \
      --store "$store" --delta "$delta/published/latest"
  fi
  printf ']' >> "$steps.tmp" && mv "$steps.tmp" "$steps"
  # The reaches published on their own schedule, for every region that has
  # them: one that should be there and is not, publish reports.
  with=""
  [ -f "$rdir/dsm2.toml" ] && with="$with --with-reach $delta/published/latest"
  [ -f "$rdir/bay.toml" ] && with="$with --with-reach $bay/published/latest"
  [ -f "$rdir/canals.toml" ] && with="$with --with-reach $canals/published/latest"
  echo "== $region: publish"
  # shellcheck disable=SC2086
  "$PY" -m fdmr_runner.publish "$rdir" "$store" "$DATA/publish/$region" \
    --calibration "$DATA/calibration/$region.json" $with || status=1
  if [ "$MODE" = nightly ]; then
    echo "== $region: nightly regime (hindcast, scores, log)"
    "$PY" -m fdmr_runner.nightly "$rdir" "$store" "$DATA" || status=1
  fi
done

# This mode's last run, beside the published cycles.
"$PY" -m fdmr_runner.cycle_runs "$DATA/publish/cycle-runs.json" "$MODE" "$started" "$status" || true
exit $status
