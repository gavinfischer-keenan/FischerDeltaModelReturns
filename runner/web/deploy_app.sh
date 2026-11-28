#!/bin/sh
# Put the built app (ui/dist) where the viewer's web server serves it:
# /srv/fdmr-data/app/<stamp>, then point /srv/fdmr-data/app/current at it in one
# step, so a visitor never sees half a build. Keeps the previous two builds.
# Run as the fdmr user once the viewer is built: runner/update.sh builds it
# (the engine's WebAssembly first, then ui/) and then runs this.
set -eu
ROOT=${FDMR_ROOT:-$HOME/fdmr}
APP=${FDMR_APP:-/srv/fdmr-data/app}
stamp=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$APP"
cp -r "$ROOT/ui/dist" "$APP/$stamp"
chmod -R a+rX "$APP/$stamp"
ln -sfn "$stamp" "$APP/.current.tmp"
mv -T "$APP/.current.tmp" "$APP/current"
ls -1d "$APP"/2* | head -n -3 | xargs -r rm -rf
echo "app $stamp is live"
