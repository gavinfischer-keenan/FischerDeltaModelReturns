#!/bin/sh
# Install the viewer's web server on the model runner (CT 705), as root:
# Debian's Caddy, our Caddyfile, checked before it is loaded. The previous
# Caddyfile is kept beside it.
set -eu
HERE=$(dirname "$0")
command -v caddy >/dev/null || { apt-get update -q && apt-get install -y -q caddy; }
mkdir -p /var/log/caddy && chown caddy:caddy /var/log/caddy
caddy validate --config "$HERE/Caddyfile" --adapter caddyfile
[ -f /etc/caddy/Caddyfile ] && cp /etc/caddy/Caddyfile "/etc/caddy/Caddyfile.before-$(date -u +%Y%m%dT%H%M%SZ)"
cp "$HERE/Caddyfile" /etc/caddy/Caddyfile
systemctl enable caddy >/dev/null 2>&1
if systemctl is-active -q caddy; then systemctl reload caddy; else systemctl start caddy; fi
systemctl is-active caddy
