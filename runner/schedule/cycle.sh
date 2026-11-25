#!/bin/sh
# One model-runner cycle for every region (specification 9.3, cadence in the
# decisions log): fetch new data, then publish. Run by systemd timers as the
# fdmr user.
#
#   cycle.sh hourly    gauges and forecasts, then publish
#   cycle.sh nightly   the same, plus channel measurements and recalibration
#
# A region's failure does not stop the next one. A cycle that is still running
# when the next is due makes the next one wait its turn (flock).
set -u
MODE=${1:-hourly}
ROOT=${FDMR_ROOT:-$HOME/fdmr}
DATA=${FDMR_DATA:-/srv/fdmr-data}
PY=${FDMR_PYTHON:-$HOME/venv/bin/python}
REGIONS=${FDMR_REGIONS:-"sf-bay-delta test-colorado"}

exec 9>"$DATA/.cycle.lock"
flock 9

case "$MODE" in
  hourly) FETCH="--days 16 --no-measurements" ;;
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
  echo "== $region: publish"
  "$PY" -m fdmr_runner.publish "$ROOT/regions/$region" "$DATA/store/fdmr.sqlite" "$DATA/publish/$region"     --calibration "$DATA/calibration/$region.json" || status=1
done
exit $status
