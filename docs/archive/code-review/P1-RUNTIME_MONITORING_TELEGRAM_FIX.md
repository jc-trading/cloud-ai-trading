# P1 Runtime Monitoring and Telegram Fix

Date: 2026-04-15
Reviewer: Codex

## Summary

Backend, Celery beat, worker, Redis, and Postgres were running, but scheduled tasks were failing before market data, indicators, system metrics, and trading signals could reliably persist.

## Root Causes

1. Celery workers did not import the full SQLAlchemy model registry. `Watchlist` relationships referencing `RiskLimit` failed during mapper initialization.
2. Celery tasks created or reused pooled async SQLAlchemy connections across fresh event loops, causing Postgres connection exhaustion and asyncpg loop errors.
3. System log middleware existed but was not registered in FastAPI, so the System Monitoring log view had no request logs to read.
4. Binance public market data collection incorrectly required API-key-backed python-binance initialization, which hit `api.binance.com` and timed out in this environment.
5. Binance kline field mapping was wrong, and indicator generation missed required `watchlist_id`.
6. MACD calculation subtracted `None` values before slow EMA data existed.

## Fixes Applied

- Loaded all ORM models in `backend/tasks/celery_app.py`.
- Added `backend/app/celery_database.py` using `NullPool` for Celery async sessions.
- Switched Celery task DB usage to `CeleryAsyncSessionLocal`.
- Reduced local Celery worker concurrency to 2 in `docker-compose.yml`.
- Made FastAPI DB pool size configurable and lowered defaults.
- Registered `SystemLogMiddleware` in `backend/app/core/middleware.py`.
- Changed Binance kline fetching to use public `data-api.binance.vision` first, without requiring API keys.
- Corrected Binance OHLCV field mapping.
- Ensured Binance client sessions close on collection errors.
- Fixed MACD calculation to skip early invalid EMA windows.
- Added `watchlist_id` when creating `TechnicalIndicator`.

## Verification

- Python compilation passed locally and inside the backend container.
- Recreated backend, Celery worker, and Celery beat containers.
- `collect_system_metrics` completed successfully and wrote `system_metrics`.
- API request logs are now written to `system_logs`.
- `collect_market_data` wrote 100 BTCUSDT 1h candles.
- `update_indicators` wrote the latest BTCUSDT technical indicator row.
- `generate_trading_signals` wrote 4 BTCUSDT signals.
- Telegram test message returned `True`.

## Remaining Behavior

Telegram trading notifications are currently sent only for `STRONG_BUY` and `STRONG_SELL` momentum signals. The verified run generated `BUY`, `SELL`, and `HOLD` signals, so no trading alert was sent by design.
