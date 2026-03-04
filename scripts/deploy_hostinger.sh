#!/usr/bin/env bash
set -euo pipefail

# Deploy script para Hostinger VPS.
# - Hace pull del repo
# - Actualiza entorno virtual
# - Instala dependencias
# - Reinicia el servicio systemd del bot

APP_DIR="${APP_DIR:-/opt/taxpy/rag-documentos}"
BRANCH="${BRANCH:-main}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SERVICE_NAME="${SERVICE_NAME:-taxpy-telegram}"

echo "[deploy] APP_DIR=$APP_DIR BRANCH=$BRANCH SERVICE=$SERVICE_NAME"

if [[ ! -d "$APP_DIR/.git" ]]; then
  echo "[deploy] ERROR: no existe repo git en $APP_DIR"
  exit 1
fi

cd "$APP_DIR"
git fetch --all --prune
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

if [[ ! -d ".venv" ]]; then
  "$PYTHON_BIN" -m venv .venv
fi

source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt

# Verificacion rapida de sintaxis para fallar antes de reiniciar.
python -m py_compile main.py config.py telegram_mvp_bot.py rag_graph/graph.py

sudo systemctl daemon-reload
sudo systemctl restart "$SERVICE_NAME"
sudo systemctl status "$SERVICE_NAME" --no-pager -l || true

echo "[deploy] OK"
