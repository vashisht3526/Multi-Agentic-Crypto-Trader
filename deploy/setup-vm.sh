#!/usr/bin/env bash
# ============================================================
# Azure VM Setup Script — Multi-Agentic Crypto Trader
# Run this ONCE after SSH-ing into a fresh Ubuntu 24.04 VM.
#
# Since the repo is PRIVATE, you need a GitHub Personal Access
# Token (PAT) to clone. The script will prompt you for it.
#
# Usage:
#   chmod +x deploy/setup-vm.sh
#   ./deploy/setup-vm.sh
# ============================================================

set -euo pipefail

APP_DIR="/home/azureuser/cryptotrader"
VENV_DIR="${APP_DIR}/.venv"
SERVICE_FILE="/etc/systemd/system/cryptotrader.service"
REPO_URL="https://github.com/vashisht3526/Multi-Agentic-Crypto-Trader.git"

echo ""
echo "=================================================="
echo "  Multi-Agentic Crypto Trader — VM Setup"
echo "=================================================="
echo ""

# ── 1. System packages ──────────────────────────────────────
echo "[1/6] Installing system dependencies..."
sudo apt update -qq
sudo apt install -y python3 python3-venv python3-pip git curl

# ── 2. Clone or update repo (PRIVATE — needs GitHub PAT) ───
echo "[2/6] Setting up project directory..."
if [ -d "${APP_DIR}/.git" ]; then
    echo "  Project already cloned. Pulling latest..."
    cd "${APP_DIR}"
    git pull origin main
else
    echo ""
    echo "  This is a PRIVATE repository. You need a GitHub Personal Access Token."
    echo "  Generate one at: https://github.com/settings/tokens"
    echo "  (Select 'repo' scope → Generate → Copy the token)"
    echo ""
    read -rp "  Enter your GitHub PAT: " GH_TOKEN

    if [ -z "${GH_TOKEN}" ]; then
        echo "  ERROR: No token provided. Cannot clone private repo."
        exit 1
    fi

    # Clone using PAT in the URL
    CLONE_URL="https://${GH_TOKEN}@github.com/vashisht3526/Multi-Agentic-Crypto-Trader.git"
    git clone "${CLONE_URL}" "${APP_DIR}"
    cd "${APP_DIR}"

    # Store credentials so future git pull doesn't ask again
    git config credential.helper store
    echo "  Repository cloned ✓"
fi

# ── 3. Python virtual environment ───────────────────────────
echo "[3/6] Creating Python virtual environment..."
if [ ! -d "${VENV_DIR}" ]; then
    python3 -m venv "${VENV_DIR}"
fi
source "${VENV_DIR}/bin/activate"
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "  Installed $(pip list 2>/dev/null | wc -l) packages."

# ── 4. Verify .env file ────────────────────────────────────
echo "[4/6] Checking .env file..."
if [ -f "${APP_DIR}/.env" ]; then
    echo "  .env file found ✓ (shipped with repo)"
else
    echo "  ⚠  WARNING: .env file missing! Copying from .env.example..."
    cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"
    echo "  Please edit it:  nano ${APP_DIR}/.env"
fi

# ── 5. Install systemd service ──────────────────────────────
echo "[5/6] Installing systemd service..."
sudo cp "${APP_DIR}/deploy/cryptotrader.service" "${SERVICE_FILE}"
sudo systemctl daemon-reload
sudo systemctl enable cryptotrader
echo "  Service installed and enabled ✓"

# ── 6. Firewall (allow API port 8000) ───────────────────────
echo "[6/6] Configuring firewall..."
if command -v ufw &>/dev/null; then
    sudo ufw allow 22/tcp   >/dev/null 2>&1 || true
    sudo ufw allow 8000/tcp >/dev/null 2>&1 || true
    echo "  Port 22 (SSH) and 8000 (API) allowed ✓"
else
    echo "  ufw not found — skipping (Azure NSG handles this)"
fi

echo ""
echo "=================================================="
echo "  ✓ Setup complete! Everything is ready."
echo "=================================================="
echo ""
echo "  Start the trading engine:"
echo "    sudo systemctl start cryptotrader"
echo ""
echo "  Watch live logs:"
echo "    sudo journalctl -u cryptotrader -f"
echo ""
echo "  Check status:"
echo "    sudo systemctl status cryptotrader"
echo ""
echo "  (Optional) Upload your DuckDB history from your PC:"
echo "    scp -i key.pem binance_ticks.duckdb azureuser@<VM-IP>:${APP_DIR}/"
echo ""
