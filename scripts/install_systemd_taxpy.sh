#!/usr/bin/env bash
set -euo pipefail

# Instalacion inicial del servicio systemd para Taxpy Telegram MVP.
# Uso (en VPS):
#   chmod +x scripts/install_systemd_taxpy.sh
#   sudo APP_DIR=/opt/taxpy/rag-documentos ./scripts/install_systemd_taxpy.sh

APP_DIR="${APP_DIR:-/opt/taxpy/rag-documentos}"
SERVICE_NAME="${SERVICE_NAME:-taxpy-telegram}"
RUN_USER="${RUN_USER:-$USER}"

SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

cat <<EOF | sudo tee "$SERVICE_FILE" >/dev/null
[Unit]
Description=Taxpy Telegram MVP Bot
After=network.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${APP_DIR}
Environment="PYTHONUNBUFFERED=1"
ExecStart=${APP_DIR}/.venv/bin/python ${APP_DIR}/main.py telegram-mvp --top-juris 6
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"
sudo systemctl status "$SERVICE_NAME" --no-pager -l || true

echo "[systemd] Servicio ${SERVICE_NAME} instalado"
