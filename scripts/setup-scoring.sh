#!/usr/bin/env bash
set -euo pipefail

# Installs a daily systemd timer that scores every run whose time is up
# (`python -m council resolve`) and emails people about their scored calls.
# Runs after the US market closes. Replaces any cron entry for the same job.
#
# Usage, on the VM from the repo folder:
#   bash scripts/setup-scoring.sh
# Idempotent: safe to re-run.

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_USER="$(whoami)"
NAME="jedi-council-score"

echo "==> Writing /etc/systemd/system/${NAME}.service"
sudo tee "/etc/systemd/system/${NAME}.service" > /dev/null <<UNIT
[Unit]
Description=Ticker Council daily scoring

[Service]
Type=oneshot
User=${SERVICE_USER}
WorkingDirectory=${REPO_DIR}
ExecStart=${REPO_DIR}/.venv/bin/python -m council resolve
UNIT

echo "==> Writing /etc/systemd/system/${NAME}.timer (daily around 21:45 UTC)"
sudo tee "/etc/systemd/system/${NAME}.timer" > /dev/null <<UNIT
[Unit]
Description=Score finished Ticker Council runs every day

[Timer]
OnCalendar=*-*-* 21:45:00 UTC
RandomizedDelaySec=10min
Persistent=true

[Install]
WantedBy=timers.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now "${NAME}.timer"

if crontab -l 2>/dev/null | grep -q "council resolve"; then
  echo "==> Removing the old cron entry for 'council resolve' (the timer replaces it)"
  crontab -l | grep -v "council resolve" | crontab -
fi

echo ""
echo "==> Done. Next run: $(systemctl list-timers "${NAME}.timer" --no-pager | sed -n 2p | awk '{print $1, $2, $3}')"
echo "    Run it now:  sudo systemctl start ${NAME}.service"
echo "    Logs:        sudo journalctl -u ${NAME}"
