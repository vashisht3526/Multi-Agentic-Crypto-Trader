# Azure VM Deployment Guide

## Overview

This project runs as a 24/7 autonomous trading engine on an **Azure Virtual Machine**.
The architecture requires a VM (not App Service/PaaS) because:
- 10 concurrent `asyncio` background loops must run indefinitely
- DuckDB requires direct local disk I/O (not network-mounted storage)
- WebSocket connections to Binance must never be interrupted

---

## Recommended Azure Configuration

| Setting                  | Value                                       |
| ------------------------ | ------------------------------------------- |
| **Subscription**         | Azure for Students ($200 credits)           |
| **Resource Group**       | `CryptoTrader-RG`                           |
| **Region**               | Canada Central (closest to AI API servers)  |
| **Availability**         | No infrastructure redundancy required       |
| **Image**                | Ubuntu Server 24.04 LTS — x64 Gen2         |
| **Size**                 | `Standard_B2as_v2` or any B-series ≥ 2vCPU / 4GB RAM |
| **OS Disk**              | 64 GB Standard SSD                          |
| **Authentication**       | SSH public key                              |
| **Username**             | `azureuser`                                 |
| **Inbound Ports**        | SSH (22), HTTP (80), Custom (8000)          |

> **Budget tip:** A B-series VM with 4 GB RAM costs ~$30–40/month.
> Your $200 credits will last 5+ months.

---

## Step-by-Step Deployment

### 1. Create the VM in Azure Portal

1. Go to **Virtual Machines → Create**.
2. Fill in the settings from the table above.
3. Download the `.pem` SSH key when prompted. Save it safely.
4. Click **Review + Create → Create**.

### 2. Connect to the VM

```bash
ssh -i "path/to/your-key.pem" azureuser@<VM_PUBLIC_IP>
```

### 3. Run the Setup Script (One Command Does Everything)

The setup script will ask for your GitHub Personal Access Token (since the repo is private),
then automatically clone the repo, install Python, create the venv, and configure systemd.

> **Get a PAT:** Go to https://github.com/settings/tokens → Generate new token (classic) → Select `repo` scope → Generate → Copy.

```bash
# Download and run the setup script directly
curl -fsSL "https://raw.githubusercontent.com/vashisht3526/Multi-Agentic-Crypto-Trader/main/deploy/setup-vm.sh" -o setup-vm.sh
chmod +x setup-vm.sh
./setup-vm.sh
```

> **Note:** Your `.env` file with API keys is included in the private repo.
> No need to upload it separately — `git clone` brings everything.

### 4. (Optional) Upload Your DuckDB History

If you want to preserve your existing trade history from your local PC:

```powershell
# Run this on your local Windows PC (PowerShell)
scp -i "path\to\your-key.pem" "binance_ticks.duckdb" azureuser@<VM_IP>:/home/azureuser/cryptotrader/
```

If you skip this, the engine will create a fresh database automatically.

### 5. Start the Engine

```bash
sudo systemctl start cryptotrader
```

> **That's it.** No code changes, no config edits, no API key uploads needed.

### 6. Verify It's Running

```bash
# Check status
sudo systemctl status cryptotrader

# Watch live logs
sudo journalctl -u cryptotrader -f

# Check if FastAPI is responding
curl http://localhost:8000/health
```

---

## Updating After Code Changes

After pushing new commits to GitHub:

```bash
cd /home/azureuser/cryptotrader
chmod +x deploy/update-vm.sh
./deploy/update-vm.sh
```

---

## Frontend Deployment (Azure Static Web Apps — Free)

The React/Vite frontend is deployed separately for $0:

1. Go to Azure Portal → **Static Web Apps → Create**.
2. **Plan:** Free.
3. **Source:** GitHub → select this repo.
4. **App location:** `/frontend`
5. **Output location:** `dist`
6. Update your frontend's API base URL to point to `http://<VM_PUBLIC_IP>:8000`.

---

## Useful Commands (on the VM)

| Command                                         | Purpose                          |
| ------------------------------------------------ | -------------------------------- |
| `sudo systemctl status cryptotrader`             | Check if engine is running       |
| `sudo systemctl restart cryptotrader`            | Restart the engine               |
| `sudo systemctl stop cryptotrader`               | Stop the engine                  |
| `sudo journalctl -u cryptotrader -f`             | Live log tail                    |
| `sudo journalctl -u cryptotrader --since "1h ago"` | Logs from last hour            |
| `df -h`                                          | Check disk space                 |
| `free -m`                                        | Check RAM usage                  |
| `htop`                                           | Interactive process monitor      |

---

## Cost Management

1. Go to Azure Portal → **Cost Management + Billing**.
2. Set a monthly budget of **$40**.
3. Set alerts at **50%**, **75%**, and **100%** of budget.
4. If you need to pause the VM to save credits: `sudo shutdown now` (or stop from Azure Portal).

> **Warning:** Stopping the VM from the Azure Portal (deallocating) stops billing for compute,
> but you still pay for the disk (~$3/month for 64GB Standard SSD).
