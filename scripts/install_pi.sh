#!/usr/bin/env bash
# One-time setup on the Pi: local mosquitto broker + attendance server on boot.
# Run as the normal user (it sudos where needed):  bash scripts/install_pi.sh
set -e

DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="$(whoami)"
SERVICE=taypro-attendance-server.service

sudo apt-get update
sudo apt-get install -y mosquitto nodejs npm python3-venv python3-pip

# mosquitto 2.x with no listener configured = localhost-only + anonymous allowed,
# which is exactly what we want: broker reachable only from this Pi.
sudo systemctl enable --now mosquitto

cd "$DIR"
# Pull the Python firmware submodule to the tip of its main branch.
git -C "$DIR" submodule update --init --remote --recursive
npm install --omit=dev
# Tracked production settings make first install and future credential changes
# automatic. Keep the runtime copy private from other users on the Pi.
install -m 600 "$DIR/config.deploy.env" "$DIR/.env"

sudo tee /etc/systemd/system/$SERVICE >/dev/null <<EOF
[Unit]
Description=Taypro attendance server (local MQTT -> MongoDB)
After=network-online.target mosquitto.service
Wants=network-online.target

[Service]
Type=simple
User=$USER_NAME
WorkingDirectory=$DIR
# reboot = pull latest node code AND latest python firmware (submodule) in one go
ExecStartPre=-/usr/bin/git -C $DIR fetch --all
ExecStartPre=-/usr/bin/git -C $DIR reset --hard origin/main
ExecStartPre=-/usr/bin/git -C $DIR submodule update --init --remote --recursive
ExecStartPre=/usr/bin/install -m 600 $DIR/config.deploy.env $DIR/.env
ExecStartPre=-/usr/bin/npm install --omit=dev
ExecStart=/usr/bin/node $DIR/server.js
Restart=always
RestartSec=8

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable $SERVICE
sudo systemctl restart $SERVICE

# Install/start the existing Python fingerprint service too. Its boot runner
# creates the venv, installs requirements and starts main.py.
python3 "$DIR/device-to-erp/scripts/install_service.py"

echo ""
echo "Done. Node logs:    journalctl -u $SERVICE -f"
echo "      Reader logs:  journalctl -u taypro-fingerprint -f"
