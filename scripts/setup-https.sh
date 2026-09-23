#!/usr/bin/env bash
set -euo pipefail

# Task #79 -- installs Caddy as a reverse proxy in front of the app, with
# automatic free HTTPS via Let's Encrypt. Run scripts/setup-oracle-vm.sh
# first (the app must already be running on localhost:$PORT).
#
# You need a domain name pointing at this machine's public IP before
# running this -- a free option is a DuckDNS subdomain (duckdns.org):
# sign in, claim a subdomain, and point it at your VM's public IP. A paid
# domain from any registrar works identically.
#
# Usage: bash scripts/setup-https.sh yourdomain.example.com

if [ -z "${1:-}" ]; then
  echo "Usage: bash scripts/setup-https.sh yourdomain.example.com"
  echo "(the domain must already have an A record pointing at this VM's public IP)"
  exit 1
fi
DOMAIN="$1"
PORT="${PORT:-8000}"

echo "==> Installing Caddy from its official apt repo"
sudo apt-get update -y
sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update -y
sudo apt-get install -y caddy

echo "==> Writing /etc/caddy/Caddyfile for $DOMAIN -> localhost:$PORT"
sudo tee /etc/caddy/Caddyfile > /dev/null <<EOF
$DOMAIN {
    reverse_proxy localhost:${PORT}
}
EOF

sudo systemctl restart caddy

echo ""
echo "==> Done."
echo "Caddy will automatically obtain (and keep renewing) a free Let's"
echo "Encrypt certificate for $DOMAIN the first time it's reachable there"
echo "over the internet on ports 80 and 443 -- see the IMPORTANT note below"
echo "if it doesn't work on the first try."
echo ""
echo "Now that HTTPS is live, harden the session cookie: set"
echo "COOKIE_SECURE=true in .env, then:"
echo "  sudo systemctl restart jedi-council"
echo ""
echo "IMPORTANT (Oracle Cloud specifically): open BOTH port 80 (Caddy needs"
echo "it for the Let's Encrypt HTTP challenge) and port 443 (actual HTTPS"
echo "traffic) as Ingress Rules in your VCN's Security List -- not just the"
echo "app's own port $PORT, which no longer needs to be open to the public"
echo "internet at all once Caddy is proxying for it."
