#!/bin/sh
# Install the model runner's schedule and development server. Run as root
# inside the model runner container (CT 705); touches nothing outside it.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
chmod 755 "$HERE/cycle.sh"
for u in fdmr-cycle@.service fdmr-hourly.timer fdmr-nightly.timer fdmr-dev-server.service; do
  install -m 644 "$HERE/$u" /etc/systemd/system/$u
done
systemctl daemon-reload
systemctl enable --now fdmr-hourly.timer fdmr-nightly.timer fdmr-dev-server.service
systemctl list-timers 'fdmr-*' --no-pager
