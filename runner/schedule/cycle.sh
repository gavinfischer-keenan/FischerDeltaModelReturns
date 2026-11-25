#!/bin/sh
# One model-runner cycle for every region (specification 9.3, cadence in the
# decisions log). Run by systemd timers as the fdmr user.
#
#   cycle.sh hourly    gauges and forecasts, then publish
#   cycle.sh nightly   the same, plus channel measurements and recalibration
#   cycle.sh delta     the same as hourly, plus a DSM2 run for every region that
#                      has one (regions/<id>/dsm2.toml) and NOAA's latest Bay
#                      forecast for every region with a bay (regions/<id>/bay.toml),
#                      every 6 hours
#
# Every publish includes the region's latest DSM2 run and Bay forecast as reaches
# of their own. A
# region's failure does not stop the next one. A cycle still running when the
# next is due makes the next one wait its turn (flock).
set -u
MODE=${1:-hourly}
ROOT=${FDMR_ROOT:-$HOME/fdmr}
DATA=${FDMR_DATA:-/srv/fdmr-data}
PY=${FDMR_PYTHON:-$HOME/venv/bin/python}
REGIONS=${FDMR_REGIONS:-"sf-bay-delta test-colorado"}

exec 9>"$DATA/.cycle.lock"
flock 9

case "$MODE" in
  hourly|delta) FETCH="--days 16 --no-measurements" ;;
  nightly) FETCH="--days 16" ;;
  *) echo "unknown mode: $MODE" >&2; exit 2 ;;
esac

cd "$ROOT/python" || exit 1
status=0
for region in $REGIONS; do
  echo "== $region: fetch ($MODE)"
  # shellcheck disable=SC2086
  "$PY" -m fdmr_runner.fetch "$ROOT/regions/$region" "$DATA/store/fdmr.sqlite" $FETCH --pause 1 || status=1
  if [ "$MODE" = nightly ]; then
    echo "== $region: calibrate"
    "$PY" -m fdmr_runner.calibrate "$ROOT/regions/$region" "$DATA/store/fdmr.sqlite" "$DATA/calibration" || status=1
  fi
  delta="$DATA/delta/$region"
  if [ "$MODE" = delta ] && [ -f "$ROOT/regions/$region/dsm2.toml" ]; then
    echo "== $region: DSM2"
    mkdir -p "$delta/runs" "$delta/published"
    "$PY" -m fdmr_runner.delta "$ROOT/regions/$region" "$DATA/store/fdmr.sqlite" "$delta/runs" \
      --publish "$delta/published" || status=1
  fi
  bay="$DATA/bay/$region"
  if [ "$MODE" = delta ] && [ -f "$ROOT/regions/$region/bay.toml" ]; then
    echo "== $region: Bay (NOAA)"
    mkdir -p "$bay/published"
    "$PY" -m fdmr_runner.bay "$ROOT/regions/$region" "$bay" --publish "$bay/published" --archive \
      --store "$DATA/store/fdmr.sqlite" --delta "$delta/published/latest" || status=1
  fi
  echo "== $region: publish"
  "$PY" -m fdmr_runner.publish "$ROOT/regions/$region" "$DATA/store/fdmr.sqlite" "$DATA/publish/$region" \
    --calibration "$DATA/calibration/$region.json" --with-reach "$delta/published/latest"     --with-reach "$bay/published/latest" || status=1
done
exit $status
