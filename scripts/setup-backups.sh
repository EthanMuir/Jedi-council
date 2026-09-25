#!/usr/bin/env bash
set -euo pipefail

# Installs a daily systemd timer that backs up the Crypt (council.db) and
# settings.db with `python -m council backup` -- see council/backup.py.
# Local copies go to ./backups (newest 14 kept). Set BACKUP_UPLOAD_URL in
# .env to also send each one off the VM (README: "Backups").
#
# Usage, on the VM from the repo folder:
#   bash scripts/setup-backups.sh
# Idempotent: safe to re-run.

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_USER="$(whoami)"
NAME="jedi-council-backup"

echo "==> Writing /etc/systemd/system/${NAME}.service"
sudo tee "/etc/systemd/system/${NAME}.service" > /dev/null <<UNIT
[Unit]
Description=Ticker Council nightly backup

[Service]
Type=oneshot
User=${SERVICE_USER}
WorkingDirectory=${REPO_DIR}
ExecStart=${REPO_DIR}/.venv/bin/python -m council backup
UNIT

echo "==> Writing /etc/systemd/system/${NAME}.timer (daily around 03:30 UTC)"
sudo tee "/etc/systemd/system/${NAME}.timer" > /dev/null <<UNIT
[Unit]
Description=Run the Ticker Council backup every night

[Timer]
OnCalendar=*-*-* 03:30:00 UTC
RandomizedDelaySec=15min
Persistent=true

[Install]
WantedBy=timers.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now "${NAME}.timer"

echo "==> Taking a first backup now"
sudo systemctl start "${NAME}.service"
sudo systemctl --no-pager status "${NAME}.service" | tail -n 5 || true
ls -lh "${REPO_DIR}/backups" | tail -n 3

echo ""
echo "==> Done. Next run: $(systemctl list-timers "${NAME}.timer" --no-pager | sed -n 2p | awk '{print $1, $2, $3}')"
echo "    Logs: sudo journalctl -u ${NAME}"
