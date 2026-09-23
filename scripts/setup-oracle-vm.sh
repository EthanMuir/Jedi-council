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

# This project needs Python 3.11+ (google-genai alone needs 3.10+), but
# several Ubuntu images Oracle still offers -- 20.04 LTS in particular --
# default `python3` to 3.8. Building the venv with that silently produces
# an environment pip then correctly refuses to install into, surfacing as
# a confusing "no matching distribution for google-genai" error that looks
# like a broken package, not a Python-version mismatch. Find (or install) a
# new enough interpreter explicitly, rather than trusting plain `python3`.
PYTHON_BIN=""
for candidate in python3.13 python3.12 python3.11; do
  if command -v "$candidate" >/dev/null 2>&1; then
    PYTHON_BIN="$candidate"
    break
  fi
done
if [ -z "$PYTHON_BIN" ] && python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
  PYTHON_BIN="python3"
fi
if [ -z "$PYTHON_BIN" ]; then
  echo "==> System Python is older than 3.11 -- installing Python 3.11 via the deadsnakes PPA"
  sudo apt-get install -y software-properties-common
  sudo add-apt-repository -y ppa:deadsnakes/ppa
  sudo apt-get update -y
  sudo apt-get install -y python3.11 python3.11-venv
  PYTHON_BIN="python3.11"
fi
echo "==> Using $PYTHON_BIN ($($PYTHON_BIN --version))"

echo "==> Creating virtualenv + installing dependencies"
cd "$REPO_DIR"
# A .venv built by an earlier, too-old interpreter (or a previous failed
# run) must be rebuilt, not reused -- `python3 -m venv` on an existing
# directory rewrites its interpreter symlink to whatever invoked it, which
# would silently downgrade a working 3.11 venv right back to 3.8 on a
# re-run otherwise.
if [ -d .venv ] && ! .venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
  echo "==> Existing .venv is on an old Python -- removing and rebuilding"
  rm -rf .venv
fi
"$PYTHON_BIN" -m venv .venv
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
