#!/usr/bin/env bash
# deploy-systemd.sh — install opencloud-docserver as a systemd unit.
# Must run as root (sudo make deploy).
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/opencloud-docserver}"
ENV_DIR="${ENV_DIR:-/etc/opencloud-docserver}"
SERVICE="opencloud-docserver.service"

if [[ ${EUID} -ne 0 ]]; then
  echo "error: run as root (sudo make deploy)" >&2
  exit 1
fi

# 1. System user (idempotent)
id -u docserver &>/dev/null || useradd -r -m docserver

# 2. Install app files
install -d -o docserver -g docserver "${APP_DIR}/data/documents"
cp -r src web config.toml Makefile "${APP_DIR}/"
cp -r .venv "${APP_DIR}/.venv" 2>/dev/null || echo "warning: no .venv found — run 'make install' first"

# 3. Install env (sample) + unit
install -d "${ENV_DIR}"
install -m600 systemd/opencloud-docserver.env "${ENV_DIR}/env"
install -m644 systemd/opencloud-docserver.service "/etc/systemd/system/${SERVICE}"

# 4. Enable + start
systemctl daemon-reload
systemctl enable "${SERVICE}"
systemctl restart "${SERVICE}"

echo "deployed: systemctl status opencloud-docserver"
echo "edit secrets:   ${ENV_DIR}/env"
echo "edit unit file: /etc/systemd/system/${SERVICE}"
