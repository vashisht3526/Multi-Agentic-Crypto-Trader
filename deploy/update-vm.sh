#!/usr/bin/env bash
# ============================================================
# Quick Update Script — Pull latest code & restart the engine
# Run this on the VM whenever you push new code to GitHub.
#
# Usage:
#   chmod +x deploy/update-vm.sh
#   ./deploy/update-vm.sh
# ============================================================

set -euo pipefail

APP_DIR="/home/azureuser/cryptotrader"

echo ""
echo "── Updating Multi-Agentic Crypto Trader ──"
echo ""

cd "${APP_DIR}"

echo "[1/4] Pulling latest code from GitHub..."
git pull origin main

echo "[2/4] Updating Python dependencies..."
source .venv/bin/activate
pip install -r requirements.txt -q

echo "[3/4] Reloading systemd (in case service file changed)..."
sudo cp "${APP_DIR}/deploy/cryptotrader.service" /etc/systemd/system/cryptotrader.service
sudo systemctl daemon-reload

echo "[4/4] Restarting engine..."
sudo systemctl restart cryptotrader

echo ""
echo "✓ Update complete. Watching logs (Ctrl+C to exit)..."
echo ""
sudo journalctl -u cryptotrader -f --no-pager -n 30
