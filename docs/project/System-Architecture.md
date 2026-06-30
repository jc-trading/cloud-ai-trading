# Cloud AI Trading — System Architecture & Design Plan

> **Version:** 2.0 (Redesigned MVP)  
> **Last Updated:** April 12, 2026  
> **Purpose:** Define the complete system architecture, design decisions, and technical stack for an AI-driven Crypto Spot trading system optimized for token efficiency and long-term stability.  
> For feature status, see `FUNCTIONAL_SPEC.md`. For task progress, see `PROGRESS.md`.

---

## 1. Project Positioning

**CloudAiTrading** is a **Crypto Spot Semi-Automatic AI Trading Research & Execution Assistance System** designed to:

- Automatically fetch real-time market data from Binance
- Automatically screen high-liquidity cryptocurrencies
- Use AI to perform technical analysis and generate trade signals
- Simulate trades automatically (paper trading)
- Notify the operator via Telegram with medium-detail updates
- Let the operator review AI analysis, voting results, simulated orders, and risk controls in a backend dashboard
- Run locally on Linux + Docker + GPU in the initial phase (Malaysia time: 00:00 AM - 07:00 AM)
- Later migrate to Tokyo VPS for 24-hour operation

**Constraints (v1):**
- No live real-money trading yet (simulate only)
- Crypto Spot only (no futures, margin, shorting)
- Max 2 open positions simultaneously
- Monthly maintenance budget: USD 50–100
- Initial research capital: USD 500

---

## 2. Core Objectives

The MVP is NOT designed to "maximize profits immediately," but to establish a system that is:

- ✅ **Stable & reliable** — runs 24/7 (later), survives restarts, compensates for offline gaps
- ✅ **Auditable** — every decision, AI output, trade is logged with full trace
- ✅ **Replayable** — can reproduce any historical analysis with exact inputs/outputs
- ✅ **Permission-limited** — hard risk controls that AI cannot breach
- ✅ **AI-transparent** — understand why each signal was generated
- ✅ **Cloud-migratable** — designed to move from local to VPS without major rewrites

---

## 3. Architecture Overview

```
                    +-------------------------------------+
                    | Binance Market Data (WebSocket)    |
                    +--------- +------------------------+
                               |
                               v
                    +-------------------------------------+
                    | Market Data Service                 |
                    | (Normalize, cache, store to DB)     |
                    +--------- +------------------------+
                               |
                +---------- ---+----------- ------+
                |                                 |
                v                                 v
    +----------------------+        +------------------------+
    | Feature Engine       |        | News Ingest Service    |
    | (Indicators, summary)|        | (Observation only)     |
    +----------+-----------+        +----------+             |
               |                               |              |
               +----------- ---+-------------- +              |
                               v                              |
                    +-------------------------------------+   |
                    | AI Orchestrator                      |   |
                    | (Route to agents, cache results)     |   |
                    +--------- +------------------------+   |
                               |                             |
                +---------- ---+----------- ------+          |
                |                                 |          |
                v                                 v          |
    +----------------+            +----------------+        |
    | Market Analyst | (60%)       | Risk Analyzer  | (40%) |
    | (Claude Sonnet)|             | (Claude Haiku) |        |
    +--------+-------+            +--------+-------+        |
             \                             /                  |
              \                           /                   |
               \                         /                    |
                \                       /                     |
                 v                     v                      |
                  +-------------------+                       |
                  | Decision Aggregator|                       |
                  | (Voting, weighting)|                       |
                  +--------+----------+                        |
                           |                                   |
                +---------- +-----------+                      |
                |                      |                      |
                v                      v                      v
    +-------------------+   +------------------+   +---------+-----+
    | Simulate Engine   |   | Telegram Notif   |   | Admin Dashboard
    | (Paper trading)   |   | (Alert delivery) |   | (Trace, audit)
    +--------+----------+   +------------------+   +----------+-----+
             |                                                 |
             +--------- +-----------------------------------+  |
                        |                                   |  |
             +----------v-----------+ +--------------------v--v---+
             | PostgreSQL 16        | | Redis 7                  |
             | (Orders, AI trace,   | | (Cache, Celery broker)   |
             |  positions, logs)    | |                          |
             +---------------------+ +-------------------------+
```

---

## 4. Technology Stack (Final)

| Layer | Technology | Reason |
|-------|-----------|--------|
| **Backend** | Python 3.12 + FastAPI | Async-native, best for quant + AI; CCXT integration; existing codebase |
| **ORM** | SQLAlchemy 2.0 (async) + Alembic | Async, versioned migrations, reliable for financial data |
| **Auth** | python-jose (JWT) + bcrypt | Stateless, no session storage needed |
| **Task Queue** | Celery 5.4 + Redis 7 (Beat) | Scheduled market pulls, AI analysis, trigger engine |
| **Frontend** | Vue 3.5 + Vite 8 | SPA, fast dev iteration, communicates via REST API |
| **UI Library** | PrimeVue 4.5 (Aura dark) + Tailwind CSS 4 | Professional trading dashboard aesthetic |
| **Charts** | TradingView Lightweight Charts 5 | Best-in-class free candlestick library |
| **State** | Pinia 3 | Vue store for auth + global state |
| **HTTP Client** | Axios | JWT interceptors, API calls |
| **Database** | PostgreSQL 16 | JSONB for configs, reliable for financial ledgers |
| **Cache / Broker** | Redis 7 | Celery broker, market data cache, result caching |
| **Exchange API** | CCXT 4 | Unified Binance adapter (OKX/Bitget in Phase 2+) |
| **Financial Calc** | pandas 2.2 + numpy 1.26 | Indicators, price series, backtesting |
| **AI** | Anthropic SDK (Claude) | Claude Sonnet (analysis) + Haiku (veto); cost-optimized |
| **Encryption** | cryptography (Fernet) | AES encryption for API keys at rest |
| **Rate Limiting** | slowapi | Request throttle on endpoints |
| **Deployment** | Docker Compose | 5 containers, consistent local → VPS |
| **Web Server** | Nginx | Reverse proxy, static frontend, SSL termination |

---

## 5. Docker Services

| Container | Image | Port | Role |
|-----------|-------|------|------|
| **backend** | cloudaitrading-backend | 8000 | FastAPI + Uvicorn |
| **celery-worker** | cloudaitrading-backend | — | Background AI analysis, trading, notifications |
| **celery-beat** | cloudaitrading-backend | — | Scheduled tasks (market pulls every 1m, analysis every 3m) |
| **postgres** | postgres:16-alpine | 5432 | Main database |
| **redis** | redis:7-alpine | 6379 | Cache + Celery broker |

---

## 6. AI Cost Optimization Strategy

**Target: < $10 USD/month for AI inference**

### 6.1 Strict Trigger Conditions
Only trigger AI analysis when:
- New 5m candle close with volatility > 1% from yesterday's ATR
- 15m breakout detected (close > 20-day high or < 20-day low)
- Volume spike (current volume > 2x 20-day avg)
- Existing position approaching key levels (SL ± 2%, TP ± 2%)
- New news with relevance score > 0.6

### 6.2 Result Caching
- Cache AI output per symbol + bar (keyed by `symbol:timeframe:close_time`)
- Reuse cache for 10 minutes (one bar window for 5m, 15 minutes for 15m)
- Deduplicate news by hash before sending to News Analyzer

### 6.3 Prompt Minimization
- Send only **structured market summary** (no verbose context)
- Output must be **strict JSON** (no natural language)
- Avoid repeating historical analysis in prompts

### 6.4 Model Selection
- **Claude Haiku** (3.5x cheaper): Risk Analyzer veto, simple checks, classification
- **Claude Sonnet** (medium cost): Market Analyst main analysis, complex signal generation
- Avoid Claude Opus (10x cost) — reserved for post-mortem reviews

### 6.5 Batch Efficiency
- Analyze up to 5 symbols in one API round-trip (shared feature context)
- Pre-compute all indicators locally before sending to Claude
- One unified "market regime" assessment per run (not per symbol)

---

## 7. Running Modes

### 7.1 Local Mode (Current Phase)
- **Environment:** Linux + Docker (user's machine)
- **Uptime:** Malaysia time 00:00 AM – 07:00 AM (6-hour window)
- **Purpose:** Research, strategy validation, simulate accumulation
- **Compensation:** On startup, pull missing candles, rebuild indicators, mark offline gaps
- **Key:** Treat simulated results during offline periods as "research-only," not live-ready

### 7.2 Cloud Mode (Phase 8)
- **Environment:** Vultr Tokyo VPS
- **Uptime:** 24/7
- **Network:** Stable, low-latency to Binance
- **Scaling:** Ready to add more AI agents, news sources, live trading (if validated)

---

## 8. Risk Controls (Hard Guardrails)

AI cannot exceed:

```yaml
# Portfolio
max_open_positions: 2
max_watch_symbols: 10
daily_loss_limit_pct: 3.0
per_trade_risk_pct: 1.5

# Sizing
default_order_size_usd: 40  # Fixed for v1
min_order_size_usd: 25
max_order_size_usd: 80

# Symbols
whitelist: [BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT, DOGEUSDT]
blacklist: []

# Mode
paper_only: true               # Simulate trading only
manual_pause: false            # Can be toggled by operator
allow_news_observation: true

# Simulation config
simulate_slippage_bps: 5      # 0.05% slippage
simulate_fee_bps: 10          # 0.1% trading fee
```

---

## 9. Database Schema (11 Tables)

### 9.1 Core Tables

**users**
- id, email, password_hash, role (SUPER_ADMIN|TRADER|VIEWER), created_at, updated_at

**symbols**
- id, symbol (BTCUSDT), base_asset, quote_asset, whitelist, blacklist, created_at

**market_candles**
- id, symbol, timeframe (1m/5m/15m/1h), open_time, open, high, low, close, volume, created_at

**market_ticks**
- id, symbol, event_time, price, quantity, created_at

### 9.2 Feature & AI Tables

**feature_snapshots**
- id, symbol, timeframe, snapshot_time, ema_20, ema_200, rsi_14, atr_14, volume_ratio, regime, summary_json, created_at

**ai_runs**
- id, trigger_type, symbol, started_at, finished_at, status, total_token_in, total_token_out, latency_ms, created_at

**ai_agent_outputs**
- id, ai_run_id, agent_name (market_analyst | risk_analyzer), output_json, confidence, latency_ms, token_in, token_out, created_at

**vote_results**
- id, ai_run_id, final_decision, final_score, reason_short, created_at

### 9.3 Trading Tables

**simulate_orders**
- id, symbol, side (buy|sell), entry_price, entry_time, size_usd, quantity, sl_price, tp_price, status, close_price, close_time, close_reason, pnl_usd, pnl_pct, created_at, updated_at

**positions**
- id, symbol, side, quantity, avg_entry_price, status (open|closed), opened_at, closed_at, pnl_usd, created_at, updated_at

### 9.4 Admin Tables

**settings**
- id, key (max_open_positions, daily_loss_limit_pct, etc.), value_json, updated_by, updated_at

---

## 10. Redis Key Design

```
# Prices & candles
price:BTCUSDT:latest           → {price, timestamp}
candles:BTCUSDT:5m:last50      → [candle1, candle2, ...]
features:BTCUSDT:15m:latest    → {ema, rsi, atr, regime}

# AI caching
ai_output:BTCUSDT:5m:123456    → {decision, confidence, ...}  [TTL: 10min]
news_analysis:hash123          → {symbols, sentiment, ...}     [TTL: 24h]

# System state
system:mode                    → "local_mode" | "cloud_mode"
system:paused                  → true | false
daily:pnl:2026-04-12          → +150.25
daily:trades:2026-04-12       → 4

# Task coordination
trigger:BTCUSDT:last_analyzed  → timestamp
```

---

## 11. API Endpoint Groups

### 11.1 Authentication
- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/refresh`
- `GET /api/v1/auth/me`

### 11.2 Market Data
- `GET /api/v1/market/tickers`
- `GET /api/v1/market/{symbol}`
- `GET /api/v1/market/{symbol}/candles?tf=5m&limit=100`
- `GET /api/v1/market/search?q=BTC`

### 11.3 Watchlist
- `GET /api/v1/watchlist`
- `POST /api/v1/watchlist`
- `DELETE /api/v1/watchlist/{symbol}`

### 11.4 AI Analysis
- `GET /api/v1/analysis/latest/{symbol}`
- `GET /api/v1/analysis?symbol=BTCUSDT&limit=20`
- `GET /api/v1/analysis/{id}/trace`

### 11.5 Simulate Trading
- `GET /api/v1/trading/positions`
- `GET /api/v1/trading/orders?status=closed&limit=50`
- `GET /api/v1/trading/portfolio/summary`

### 11.6 Admin
- `GET /api/v1/admin/dashboard/summary`
- `GET /api/v1/admin/settings/risk`
- `PUT /api/v1/admin/settings/risk`
- `POST /api/v1/admin/system/pause`
- `POST /api/v1/admin/system/resume`
- `GET /api/v1/admin/audit-logs`

---

## 12. Telegram Notifications

**Event Types:**
1. **Opportunity Detected** — AI found a buy signal
2. **Risk Blocked** — Trade rejected by risk veto
3. **Simulate Opened** — Order filled
4. **Simulate Closed** — Position closed (TP/SL/manual)
5. **Daily Summary** — Session end report

---

## 13. Development Phases (8 Phases)

### Phase 1: Core Infrastructure
- Docker Compose, FastAPI skeleton, JWT auth (3 roles)
- PostgreSQL + Redis initialization

### Phase 2: Market Data & Features
- Binance WebSocket, OHLCV storage
- Technical indicators (EMA, RSI, ATR, BB)
- Feature summary generation

### Phase 3: AI Orchestrator (2 Agents)
- Anthropic SDK (Sonnet + Haiku)
- Market Analyst + Risk Analyzer
- Voting & aggregation

### Phase 4: Simulate Engine
- Order placement, position tracking
- P&L calculation with slippage/fees
- Order history & replay

### Phase 5: Telegram & Dashboard
- Telegram bot + Admin UI
- AI Trace viewer
- Risk settings UI

### Phase 6: News Observation (Optional)
- News ingest + deduplication
- News Analyzer (observation only)

### Phase 7: Local 24-Hour Stability
- Startup compensation
- Graceful shutdown
- Offline-gap marking
- Daily summaries

### Phase 8: Cloud Migration
- Vultr Tokyo VPS deployment
- SSL/TLS
- 24/7 operation monitoring

---

## 14. Success Metrics (MVP Acceptance)

- ✅ Binance WebSocket feeds real-time data
- ✅ Indicators match TradingView
- ✅ AI agents output JSON every 3–5 minutes
- ✅ Simulate engine P&L matches manual calculations
- ✅ Telegram delivery < 5 seconds
- ✅ Dashboard shows complete audit trail
- ✅ Runs locally 6 hours/day for 7 days without crashes
- ✅ Monthly token spend < $10 USD
- ✅ VPS migration recovers 100% of data

---

## 15. Implementation Principles

1. **Stability first, complexity second**
2. **Simulate always, live never** (until validated)
3. **Traceable always** — Every decision logged
4. **Permission-hard always** — Hard limits > soft limits
5. **Local first, cloud later** — Validate locally before VPS
6. **Cost-aware always** — Clear ROI for every API call

---

**End of System Plan — v2.0**
