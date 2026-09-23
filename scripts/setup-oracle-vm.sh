#!/usr/bin/env bash
set -euo pipefail

# Task #79 -- one-time setup for running The High Council as a persistent,
# auto-restarting systemd service on a fresh Ubuntu/Debian VM (written for
# Oracle Cloud's Always Free ARM tier, but this part is generic -- nothing
# here is Oracle-specific except the note at the end about their Security
# List). Idempotent: safe to re-run after a `git pull` to pick up new
# dependencies and restart the service.
#
# Usage: clone this repo onto the VM, cd into it, then:
#   bash scripts/setup-oracle-vm.sh

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_USER="$(whoami)"
SERVICE_NAME="jedi-council"
PORT="${PORT:-8000}"

echo "==> Repo:    $REPO_DIR"
echo "==> Service: runs as $SERVICE_USER, port $PORT"

if [ ! -f "$REPO_DIR/.env" ]; then
  echo ""
  echo "ERROR: $REPO_DIR/.env not found."
  echo "Copy .env.example to .env and fill it in first -- at minimum"
  echo "ANTHROPIC_API_KEY and APP_PASSWORD (see the README's hosting"
  echo "section for why APP_PASSWORD matters before this is reachable"
  echo "from the public internet)."
  exit 1
fi

if ! grep -qE '^APP_PASSWORD=.+' "$REPO_DIR/.env"; then
  echo ""
  echo "WARNING: APP_PASSWORD is blank in .env -- this app will be"
  echo "reachable from the public internet with NO authentication once"
  echo "this script finishes. Anyone who finds the URL can run"
  echo "deliberations billed to your API keys and read your prediction"
  echo "history. Ctrl-C now and set APP_PASSWORD first unless that's"
  echo "genuinely what you want."
  read -r -p "Continue anyway? [y/N] " reply
  case "$reply" in
    [yY]*) ;;
    *) exit 1 ;;
  esac
fi

echo "==> Installing Python + venv tooling"
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip

echo "==> Creating virtualenv + installing dependencies"
cd "$REPO_DIR"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e ".[dev]"

echo "==> Writing systemd unit at /etc/systemd/system/${SERVICE_NAME}.service"
sudo tee "/etc/systemd/system/${SERVICE_NAME}.service" > /dev/null <<EOF
[Unit]
Description=The High Council
After=network.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${REPO_DIR}
ExecStart=${REPO_DIR}/.venv/bin/uvicorn council.api.main:app --host 0.0.0.0 --port ${PORT}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

echo "==> Enabling + starting the service"
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

if command -v ufw >/dev/null 2>&1 && sudo ufw status | grep -q "Status: active"; then
  echo "==> Opening port $PORT in ufw"
  sudo ufw allow "${PORT}/tcp"
fi

echo ""
echo "==> Done."
echo "    Status: sudo systemctl status $SERVICE_NAME"
echo "    Logs:   sudo journalctl -u $SERVICE_NAME -f"
echo ""
echo "IMPORTANT (Oracle Cloud specifically): the VM's own firewall isn't"
echo "the only thing blocking traffic -- Oracle blocks incoming ports at"
echo "the network level by default via your VCN's Security List / Network"
echo "Security Group. Add an Ingress Rule there for TCP port $PORT (or"
echo "80/443 if you run scripts/setup-https.sh next) before this is"
echo "reachable from outside the VM. See the README's hosting section."
