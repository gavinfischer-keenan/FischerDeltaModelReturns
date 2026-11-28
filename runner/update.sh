#!/bin/sh
# Rebuild and put live everything an update to ~/fdmr needs (runner/README.md):
# the engine's Python binding, its WebAssembly, the viewer, then the viewer's
# deploy. Run as the fdmr user. Stops at the first step that fails, before
# anything is put live.
set -eu
ROOT=${FDMR_ROOT:-$HOME/fdmr}
PY=${FDMR_PYTHON:-$HOME/venv/bin/python}
# shellcheck source=/dev/null
. "$HOME/.cargo/env"
cd "$ROOT"
"$PY" -m pip install -q ./crates/fdmr-py
wasm-pack build crates/fdmr-wasm --target web --release --out-dir ../../ui/src/compute/pkg
cd ui
npm ci --silent
npm run -s check
rm -rf dist
npm run -s build
[ -f dist/index.html ] || { echo "no build: nothing put live" >&2; exit 1; }
sh "$ROOT/runner/web/deploy_app.sh"
