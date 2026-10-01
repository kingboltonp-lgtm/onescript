#!/usr/bin/env bash
# One-time setup on an Ubuntu EC2 instance (run as ubuntu, from /opt/onescript).
set -euo pipefail
cd /opt/onescript
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
[ -f .env ] || { cp .env.example .env; echo "Fill in /opt/onescript/.env"; }
sudo cp deploy/range-breakout.service deploy/range-breakout.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now range-breakout.timer
systemctl list-timers range-breakout.timer
