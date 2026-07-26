# quant/ — CAT deterministic backtest + strategy engine (R0)

Framework-free core. Runs in a **host venv** during R0 (no Docker needed); the
`engine/` pure functions get imported into the backend container in R1 and shared
verbatim between backtest and live (架构铁律 ①).

## Setup (host, Python 3.11+)

```bash
cd cloud-ai-trading
python3 -m venv quant/.venv
source quant/.venv/bin/activate
pip install -r quant/requirements.txt
```

## Run tests

```bash
# from repo root, with the venv active
pytest quant/tests
```

## Layout

- `config.py` — read-only constants (paths, universe, Master Settings defaults)
- `data/` — fetch · store (Parquet) · manifest · corporate_actions · calendar ·
  universe · **`bars.get_bars()`** (the only market-data entry point)
- `engine/` — indicators · signal · strategy · funnel · sizing · exits (all pure)
- `backtest/` — costs · simulator (thin, <500 lines) · metrics · walkforward · bias_checks
- `research/` — R0-9 calibration + scoreboard report

Data lands in `cat-data/` at repo root (gitignored). API keys are read from the
repo `.env` — never hardcoded, never committed.

Design authority: `~/jiacong-ai/my-working-draft/CAT_merged_plan_v2.0.md` (rev3).
Execution manual: `~/jiacong-ai/my-working-draft/CAT_execution_plan_R0-R1.md`.
