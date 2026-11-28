#!/bin/sh
# Rebuild and put live everything an update to ~/fdmr needs (runner/README.md):
# the engine's Python binding, its WebAssembly, the viewer, then the viewer's
# deploy. Run as the fdmr user. Stops at the first step that fails, before
# anything is put live.
set -eu
ROOT=${FDMR_ROOT:-$HOME/fdmr}
DATA=${FDMR_DATA:-/srv/fdmr-data}
PY=${FDMR_PYTHON:-$HOME/venv/bin/python}
# shellcheck source=/dev/null
. "$HOME/.cargo/env"
cd "$ROOT"
# The binding is replaced only between cycles: a cycle that had the old one
# loaded and then imported more of the engine would mix the two (as code
# copied in mid-cycle did on 28 Nov 2026). The same lock as cycle.sh's.
flock -w 3000 "$DATA/.cycle.lock" "$PY" -m pip install -q ./crates/fdmr-py
wasm-pack build crates/fdmr-wasm --target web --release --out-dir ../../ui/src/compute/pkg
cd ui
npm ci --silent
npm run -s check
rm -rf dist
npm run -s build
[ -f dist/index.html ] || { echo "no build: nothing put live" >&2; exit 1; }
sh "$ROOT/runner/web/deploy_app.sh"
