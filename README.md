# BTC Orderflow CrewAI Pipeline

This project runs multiple CrewAI agents on live Binance tick data:

1. `BinanceTickIngestionAgent` connects to Binance `@trade` websocket and stores tick-level trades.
2. `Orderflow Calculator Agent` computes orderflow snapshots.
3. `Orderflow Analyst Agent` classifies orderflow regime.
4. `BTC Opportunity Scout Agent` emits BTC trading opportunities.
5. `Signal Challenger Agent` audits whether each generated signal is logically consistent.
6. `Signal Performance Reviewer Agent` evaluates mature signals as win/loss/expired and updates learning memory.
7. `Crew Event Logger` stores agent-by-agent progress and structured debate artifacts for the dashboard.

All data is stored in a fast local DuckDB database.

*****************************

# Start everything (from any terminal):
powershell -ExecutionPolicy Bypass -File .\start.ps1

# Stop everything:
powershell -ExecutionPolicy Bypass -File .\stop.ps1

**************************

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill your keys/config.

Important:
- Use `GROQ_API_KEY` + optional `GROQ_MODEL` to run on Groq.
- If `GROQ_API_KEY` is set, it is used automatically.
- Otherwise it falls back to `OPENAI_API_KEY` + `OPENAI_MODEL`.
- If the DB file is locked by another process, startup waits `DB_LOCK_WAIT_SECONDS`.
- `DB_LOCK_FALLBACK=0` is the safer default because it preserves one continuous performance database.
- If you explicitly set `DB_LOCK_FALLBACK=1`, startup can use a timestamped fallback DB file, but that fragments learning history.
- `MAX_ALLOWED_TICK_GAP_SECONDS` blocks analysis/review when the local tick stream has suspicious gaps.
- `SIGNAL_HORIZON_SECONDS` controls how long each signal is monitored before marking it `EXPIRED`.
- `SIGNAL_EVAL_RETRY_SECONDS` controls how long to wait before retrying a signal review when data coverage is poor.
- `MAX_SIGNAL_EVAL_DEFERRALS` caps how many times a review is deferred before the signal is marked `NO_DATA`.
- `LEARNING_MIN_SAMPLES` controls how many past similar trades are required before applying avoid/promote learning bias.
- If DuckDB fails replaying a `.wal` file on startup, the app archives the unreplayed WAL into `wal_recovery/` and retries against the base DB.
- `DASHBOARD_SNAPSHOT_PATH` is the JSON snapshot published by the running pipeline for the dashboard and inspector.
- `CREW_VERBOSE=1` prints the step-by-step CrewAI task/tool flow in the terminal.

## Run

```bash
python main.py
```

The pipeline will:
- start Binance ingestion for `SYMBOL` (default `BTCUSDT`),
- wait until enough tick data is available,
- run CrewAI agents repeatedly every `ANALYSIS_INTERVAL_SECONDS`,
- skip analysis when recent local tick coverage is unhealthy,
- persist snapshots, analyses, opportunities, signal challenge verdicts, signal outcomes, crew events, and learning memory to DuckDB (`DB_PATH`),
- publish a lock-free dashboard snapshot JSON to `DASHBOARD_SNAPSHOT_PATH`.

## Dashboard

Launch the local Streamlit dashboard in a second terminal:

```bash
streamlit run dashboard.py
```

The dashboard reads the published snapshot JSON from `DASHBOARD_SNAPSHOT_PATH`, so it does not need to open the live DuckDB file.
It shows:
- live BTC tick tape and market-data health
- recent signals with `WIN` / `LOSS` / `EXPIRED` / `OPEN` / `NO_DATA`
- scout vs challenger debate artifacts
- crew timeline and agent-by-agent progress
- signal learning memory
- challenger calibration learning

## Inspect Signals And Logic

Use the inspection script when you want to see exactly why a signal was generated, how the challenger scored it, and whether it later won or lost:

```bash
python inspect_pipeline.py --limit 20
python inspect_pipeline.py --signal-id <signal_id>
```

If the live DB is locked by the running writer process on Windows, the inspector automatically falls back to the published snapshot file.

The output includes:
- recent signal history with `WIN` / `LOSS` / `EXPIRED` / `OPEN` / `NO_DATA`
- challenger verdicts (`APPROVE` / `WEAK_APPROVE` / `REJECT`)
- learning profiles grouped by side/regime
- recent crew events
- challenger calibration profiles
- the full stored decision trace for the latest or selected signal

## Database Tables

- `ticks`
- `orderflow_snapshots`
- `orderflow_analysis`
- `trading_opportunities`
- `trade_signals`
- `signal_challenges`
- `crew_events`
- `signal_evaluations`
- `learning_memory`
- `challenger_learning_memory`




● Crew cycle failed
litellm.BadRequestError: GroqException - {"error":{"message":"Failed to call a function. Please adjust your prompt. See 'failed_generation' for more details.","type":"invalid_request_error","code":"tool_use_failed","failed_generation":"The tool result shows a performance summary with learning-memory update details. Here is the actual complete content as the final answer:\n\n{\n \"response\": {\n \"challenger\": {\n \"verdict_breakdown\": [\n {\n \"avg_challenge_score\": 1.0,\n \"challenge_verdict\": \"APPROVE\",\n \"expired\": 7,\n \"losses\": 1,\n \"open_signals\": 8,\n \"total_signals\": 22,\n \"win_rate\": 0.0,\n \"wins\": 0\n },\n {\n \"avg_challenge_score\": null,\n \"challenge_verdict\": \"PENDING\",\n \"expired\": 1,\n \"losses\": 0,\n \"open_signals\": 0,\n \"total_signals\": 2,\n \"win_rate\": 0.0,\n \"wins\": 0\n }\n ]\n"}}

tell me is it really trading or just simalating results, beacuase you make some changes and tests it only then it makes signlas and all data with profit and loss also shwn only then afterwhen i start the system  it just shown nothing, and after your last changes it just genertaed losses, what did you do? or it is just calculating these signals and profit, loss from past data?

can you do something so that all the backend activity from data fetching to signal generation all agents activity will be visible in frontend can develope specific module for that?