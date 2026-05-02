# Multi-Agentic Crypto Trader

An autonomous, multi-agent BTC trading system powered by CrewAI, LiteLLM, and real-time Binance market data. The system uses a 7-gate hierarchical execution backbone with structural exit management, multi-timeframe trend alignment, and AI-powered advisory agents.

## Architecture

The engine runs **10 concurrent async loops** inside a single `asyncio.gather()`:

| Loop                    | Purpose                                            |
| ----------------------- | -------------------------------------------------- |
| `binance_ingestion`     | WebSocket stream of live BTC trades from Binance   |
| `l2_depth_stream`       | Level-2 order book depth snapshots                 |
| `funding_rate_poller`   | Perpetual funding rate tracking                    |
| `timeframe_analysis`    | Multi-timeframe trend engine (1m → 1h)             |
| `analysis_loop`         | Hierarchical backbone: 7-gate signal generation    |
| `trade_reaper`          | Structural exit evaluator (SL/TP/trailing/reversal)|
| `dashboard_snapshot`    | Periodic JSON snapshot for the frontend            |
| `auto_tune`             | Weekly parameter auto-tuning from trade history    |
| `fastapi_server`        | REST + WebSocket API on port 8000                  |

All data is persisted in a local **DuckDB** database.

## Tech Stack

- **Backend:** Python 3.12, FastAPI, asyncio, DuckDB, CrewAI, LiteLLM
- **Frontend:** React 19, Vite 8, Recharts
- **AI Providers:** Groq (primary), Gemini (fallback), OpenAI (optional)

---

## Quick Start (Local / Windows)

```bash
# 1. Setup
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 2. Frontend
cd frontend && npm install && cd ..

# 3. Configure
copy .env.example .env
# Edit .env with your API keys

# 4. Run
powershell -ExecutionPolicy Bypass -File .\start.ps1

# 5. Stop
powershell -ExecutionPolicy Bypass -File .\stop.ps1
```

- **Backend API:** http://localhost:8000
- **Frontend UI:** http://localhost:5173

---

## Cloud Deployment (Azure VM)

This system is designed to run 24/7 on an Azure Linux VM. Full step-by-step instructions are in [`deploy/DEPLOYMENT.md`](deploy/DEPLOYMENT.md).

**Quick summary:**

```bash
# On a fresh Ubuntu 24.04 Azure VM:
git clone https://github.com/vashisht3526/Multi-Agentic-Crypto-Trader.git cryptotrader
cd cryptotrader
chmod +x deploy/setup-vm.sh
./deploy/setup-vm.sh

# Upload .env and database, then:
sudo systemctl start cryptotrader
sudo journalctl -u cryptotrader -f
```

**Recommended VM:** B-series (≥2 vCPU, 4GB RAM), Ubuntu 24.04 LTS x64, Canada Central region.

---

## Configuration

Copy `.env.example` to `.env` and fill in your keys:

| Variable                           | Description                                      |
| ---------------------------------- | ------------------------------------------------ |
| `SYMBOL`                           | Trading pair (default: `BTCUSDT`)                |
| `DB_PATH`                          | DuckDB file path                                 |
| `GROQ_API_KEY` / `GROQ_MODEL`     | Primary LLM provider                             |
| `GEMINI_API_KEY_1..3`              | Fallback LLM keys (round-robin)                  |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | Optional OpenAI fallback                         |
| `ANALYSIS_INTERVAL_SECONDS`        | How often the backbone runs (default: 45s)       |
| `LOOKBACK_SECONDS`                 | Tick data window for analysis                    |
| `CREW_VERBOSE`                     | Set to `1` for detailed agent logs               |

See `.env.example` for the full list.

---

## Project Structure

```
├── main.py                 # Entry point — launches all async loops
├── engine/
│   ├── backbone.py         # 7-gate hierarchical execution backbone
│   ├── config.py           # Trading constants and thresholds
│   ├── database.py         # DuckDB persistence layer
│   ├── settings.py         # Environment config loader
│   ├── agents/             # CrewAI advisory agents
│   ├── analysis/           # Multi-timeframe analysis engine
│   └── data/               # Binance data ingestion
├── backend/
│   ├── api.py              # FastAPI REST + WebSocket server
│   ├── schemas.py          # Pydantic response models
│   └── ws_manager.py       # WebSocket connection manager
├── frontend/               # React + Vite dashboard
├── scripts/                # Maintenance and diagnostic scripts
├── deploy/                 # Azure VM deployment files
│   ├── DEPLOYMENT.md       # Full deployment guide
│   ├── setup-vm.sh         # One-time VM setup script
│   ├── update-vm.sh        # Pull & restart script
│   └── cryptotrader.service # Systemd unit file
├── start.ps1               # Windows launcher
├── stop.ps1                # Windows stopper
└── requirements.txt        # Python dependencies
```

---

## Useful Scripts

```bash
# Diagnostic: inspect signal pipeline
python scripts/inspect_pipeline.py --limit 20

# Diagnostic: check gate pass rates
python scripts/diagnose_gates.py

# Maintenance: clean old data
python scripts/clean_database.py

# Maintenance: backfill hourly performance
python scripts/backfill_hourly_performance.py
```

---

## Database Tables

| Table                       | Purpose                                    |
| --------------------------- | ------------------------------------------ |
| `ticks`                     | Raw Binance trade ticks                    |
| `orderflow_snapshots`       | Computed orderflow metrics                 |
| `orderflow_analysis`        | Regime classification results              |
| `trading_opportunities`     | Generated opportunity signals              |
| `trade_signals`             | Active/closed trade records with PnL       |
| `signal_challenges`         | Challenger agent audit results             |
| `signal_evaluations`        | Historical evaluation outcomes             |
| `crew_events`               | Agent activity timeline                    |
| `learning_memory`           | Side/regime performance memory             |
| `challenger_learning_memory`| Challenger calibration data                |
| `hourly_performance`        | Aggregated hourly performance stats        |

---

## License

Private — All rights reserved.