#!/usr/bin/env bash
set -euo pipefail

# Moves the site to a new domain once Caddy is already set up (see
# setup-https.sh). Serves the app at DOMAIN, sends www.DOMAIN there too,
# and permanently redirects every OLD_DOMAIN given -- so old bookmarks and
# links keep working. Caddy gets the new HTTPS certificates by itself.
#
# Before running: add an A record for DOMAIN (and for www) at your
# registrar, pointing at this VM's public IP, and wait until
#   getent hosts DOMAIN
# prints that IP.
#
# Usage: bash scripts/set-domain.sh tickercouncil.com ethanscouncil.duckdns.org

if [ -z "${1:-}" ]; then
  echo "Usage: bash scripts/set-domain.sh NEW_DOMAIN [OLD_DOMAIN ...]"
  exit 1
fi
DOMAIN="$1"
shift
PORT="${PORT:-8000}"

if ! getent hosts "$DOMAIN" > /dev/null; then
  echo "!! $DOMAIN doesn't point anywhere yet. Add its A record at your registrar,"
  echo "   wait a few minutes, then run this again."
  exit 1
fi

CADDYFILE=/etc/caddy/Caddyfile
if [ -f "$CADDYFILE" ]; then
  sudo cp "$CADDYFILE" "$CADDYFILE.bak"
  echo "==> Saved the old Caddyfile as $CADDYFILE.bak"
fi

{
  echo "$DOMAIN {"
  echo "    reverse_proxy localhost:${PORT}"
  echo "}"
  echo ""
  echo "www.$DOMAIN {"
  echo "    redir https://$DOMAIN{uri} permanent"
  echo "}"
  for OLD in "$@"; do
    echo ""
    echo "$OLD {"
    echo "    redir https://$DOMAIN{uri} permanent"
    echo "}"
  done
} | sudo tee "$CADDYFILE" > /dev/null

echo "==> New Caddyfile:"
cat "$CADDYFILE"

if ! sudo caddy validate --config "$CADDYFILE" --adapter caddyfile; then
  echo "!! Caddy rejected the new config; putting the old one back."
  [ -f "$CADDYFILE.bak" ] && sudo cp "$CADDYFILE.bak" "$CADDYFILE"
  exit 1
fi
sudo systemctl reload caddy

echo ""
echo "==> Done. Open https://$DOMAIN -- the first visit can take up to a minute"
echo "while Caddy fetches the certificate. You'll need to sign in again, since"
echo "sign-ins belong to the old address."
if ! getent hosts "www.$DOMAIN" > /dev/null; then
  echo ""
  echo "Note: www.$DOMAIN has no record yet, so it won't work until you add one"
  echo "(an A record for 'www' with the same IP)."
fi
