# Cloud AI Trading — Functional Specification (MVP v1)

> **Purpose:** Master list of all features and modules in the MVP.  
> **Build Status:** Fresh start — all features to be implemented from scratch.  
> **Last Updated:** April 12, 2026

---

## Status Legend

| Icon | Meaning |
|------|---------|
| 🟢 | **Planned** — Ready to build in phase sequence |
| 🟠 | **Phase X+** — Deferred to later phases (not MVP) |

---

## 1. Authentication & User Management

| Function | Status | Phase | Notes |
|----------|--------|-------|-------|
| User Registration | 🟢 | 1 | Email + password, JWT tokens |
| User Login | 🟢 | 1 | Email + password, access + refresh tokens |
| JWT Token Refresh | 🟢 | 1 | Silent refresh via /auth/refresh |
| 3-Tier Role System | 🟢 | 1 | SUPER_ADMIN, TRADER, VIEWER |
| RBAC Permission Gates | 🟢 | 1 | Per-role permission checks on all endpoints |
| User Profile View | 🟢 | 1 | Read own email, role, created_at |
| Email Verification | 🟠 | Phase 4+ | Send verification link on register |
| Password Reset | 🟠 | Phase 4+ | Forgot password via email link |
| Two-Factor Authentication (TOTP) | 🟠 | Phase 5+ | Optional 2FA for SUPER_ADMIN |
| Demo / Guest Mode | 🟠 | Phase 4+ | Read-only access, no login required |

---

## 2. Market Data

| Function | Status | Phase | Notes |
|----------|--------|-------|-------|
| Binance WebSocket (ticker + trade) | 🟢 | 2 | Real-time price and volume |
| Binance REST OHLCV (1m/5m/15m/1h) | 🟢 | 2 | Candle history via CCXT |
| Symbol Search | 🟢 | 2 | Search by symbol name |
| Live Ticker (24h change, volume) | 🟢 | 2 | Via Binance REST /ticker endpoint |
| Market Candle Storage (PostgreSQL) | 🟢 | 2 | Persist OHLCV for analysis |
| Market Tick Storage (optional v1) | 🟠 | Phase 4+ | Store every trade tick (high volume) |

---

## 3. Watchlist

| Function | Status | Phase | Notes |
|----------|--------|-------|-------|
| Create Default Watchlist | 🟢 | 2 | Seeded with 5 symbols: BTC, ETH, SOL, XRP, DOGE |
| Add Symbol to Watchlist | 🟢 | 2 | Backend + Frontend |
| Remove Symbol from Watchlist | 🟢 | 2 | Backend + Frontend |
| List Watchlist Items with Prices | 🟢 | 2 | Dedicated watchlist page |
| Symbol Whitelist Enforcement | 🟢 | 3 | AI cannot trade outside whitelist |

---

## 4. Technical Analysis (Indicators)

| Function | Status | Phase | Notes |
|----------|--------|-------|-------|
| EMA (20, 200 period) | 🟢 | 2 | Exponential moving average |
| RSI (14 period) | 🟢 | 2 | Relative strength index |
| ATR (14 period) | 🟢 | 2 | Average true range (volatility) |
| Bollinger Bands (20, 2 std dev) | 🟢 | 2 | Upper/middle/lower bands |
| MACD (12/26/9) | 🟢 | 2 | Moving average convergence divergence |
| Volume Ratio (current / 20d avg) | 🟢 | 2 | Spike detection |
| Market Regime Detection | 🟢 | 2 | Uptrend/downtrend/range (EMA-based) |
| Feature Summary JSON | 🟢 | 2 | Structured input for AI agents |

---

## 5. AI Orchestration (2 Agents)

### 5.1 Market Analyst Agent

| Function | Status | Phase | Notes |
|----------|--------|-------|-------|
| Receives technical summary + regime | 🟢 | 3 | Input: symbol, candles, indicators |
| Outputs structured JSON decision | 🟢 | 3 | decision: buy/hold/sell, confidence: 0.0–1.0 |
| Suggests entry zone | 🟢 | 3 | min_price, max_price (2% range) |
| Suggests take-profit & stop-loss | 🟢 | 3 | tp_price, sl_price |
| Provides reasoning (short) | 🟢 | 3 | Explanation for the signal |
| Uses Claude Sonnet (cost-optimized) | 🟢 | 3 | For complex market reasoning |

### 5.2 Risk Analyzer Agent

| Function | Status | Phase | Notes |
|----------|--------|-------|-------|
| Receives trade intent from Market Analyst | 🟢 | 3 | Input: symbol, action, size_usd, account state |
| Assesses risk level (low/medium/high) | 🟢 | 3 | Based on position count, daily P&L, volatility |
| Can VETO the trade (veto=true) | 🟢 | 3 | Hard blocker if conditions violated |
| Suggests position size multiplier | 🟢 | 3 | 0.5–1.0 (scale down if risky) |
| Provides reasoning | 🟢 | 3 | Why approved or rejected |
| Uses Claude Haiku (3.5x cheaper) | 🟢 | 3 | For rule-based risk checks |

### 5.3 AI Orchestrator Core

| Function | Status | Phase | Notes |
|----------|--------|-------|-------|
| Trigger detection (strict conditions) | 🟢 | 3 | Spike, breakout, volatility, key levels |
| Result caching (10-min window) | 🟢 | 3 | Skip re-analysis for same bar |
| Agent routing (Sonnet → Haiku) | 🟢 | 3 | Sequential call, aggregate results |
| Token usage tracking | 🟢 | 3 | Log input + output tokens per agent |
| Latency measurement | 🟢 | 3 | Track API response time |
| Store AI run + agent outputs in DB | 🟢 | 3 | Full audit trail |

---

## 6. Decision Aggregation & Voting

| Function | Status | Phase | Notes |
|----------|--------|-------|-------|
| Weight votes (Market 60%, Risk 40%) | 🟢 | 3 | Aggregate confidence scores |
| Risk VETO rules | 🟢 | 3 | Block if: veto=true OR 2 positions open OR daily loss hit OR paused |
| Generate final trade intent | 🟢 | 3 | symbol, action, entry_zone, sl, tp, size_usd |
| Store vote result in DB | 🟢 | 3 | For replay and audit |

---

## 7. Simulate Engine (Paper Trading)

| Function | Status | Phase | Notes |
|----------|--------|-------|-------|
| Receive trade intent | 🟢 | 4 | From decision aggregator |
| Place simulate order | 🟢 | 4 | Record entry_price, entry_time, symbol, size |
| Track position status | 🟢 | 4 | open → monitoring → closed |
| Monitor stop-loss trigger | 🟢 | 4 | Close position if price hits SL |
| Monitor take-profit trigger | 🟢 | 4 | Close position if price hits TP |
| Calculate P&L (gross) | 🟢 | 4 | (exit_price - entry_price) * quantity |
| Apply slippage & fees (net P&L) | 🟢 | 4 | Slippage: 5bps, Fee: 10bps |
| Store order history | 🟢 | 4 | All opens and closes in DB |
| Daily PnL aggregation | 🟢 | 4 | Sum all closed positions + unrealized |
| Position status flow | 🟢 | 4 | pending → opened → monitoring → closed_tp/closed_sl/closed_manual |

---

## 8. Risk Controls (Hard Guardrails)

| Control | Status | Phase | Notes |
|---------|--------|-------|-------|
| Max open positions (2) | 🟢 | 4 | AI cannot exceed 2 live positions |
| Daily loss limit (3%) | 🟢 | 4 | Stop trading if daily P&L < -$15 (3% of $500) |
| Per-trade risk limit (1.5%) | 🟢 | 4 | Max 7.50 USD per trade size |
| Whitelist enforcement | 🟢 | 4 | Only trade BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT, DOGEUSDT |
| Manual pause/resume | 🟢 | 4 | Admin can stop AI without code restart |
| Simulate-only mode (paper_only=true) | 🟢 | 4 | No live order placement in v1 |
| Slippage & fee simulation | 🟢 | 4 | Realistic P&L calculation |

---

## 9. Notifications (Telegram)

| Event | Status | Phase | Notes |
|-------|--------|-------|-------|
| Opportunity Detected | 🟢 | 5 | AI found buy signal, risk evaluating |
| Risk Blocked | 🟢 | 5 | Trade rejected by risk veto |
| Simulate Opened | 🟢 | 5 | Order filled: symbol, entry, SL, TP, reason |
| Simulate Closed | 🟢 | 5 | Position closed: symbol, exit, P&L, close reason |
| Daily Summary | 🟢 | 5 | # trades, wins, losses, P&L, open positions |
| System Pause / Resume | 🟠 | Phase 5+ | Notify when AI halted or resumed |

---

## 10. Admin Dashboard

| Page | Status | Phase | Notes |
|------|--------|-------|-------|
| Dashboard (Summary) | 🟢 | 5 | Key stats: trades today, P&L, open positions, risk blocks |
| Watchlist Manager | 🟢 | 2 | View/add/remove symbols |
| AI Trace Center | 🟢 | 5 | View each AI run: triggers, input, agent outputs, voting, result |
| Simulate Orders | 🟢 | 5 | Historical orders, filter by symbol/time, view P&L |
| Positions Monitor | 🟢 | 5 | Current open positions, unrealized P&L |
| Risk Settings | 🟢 | 5 | Max positions, daily loss limit, whitelist, pause toggle |
| News Observation (optional) | 🟠 | Phase 6+ | Log of news items + relevance to symbols |
| Admin Audit Log | 🟠 | Phase 7+ | Who changed risk settings, when, before/after values |

---

## 11. News Observation (Optional v1)

| Function | Status | Phase | Notes |
|----------|--------|-------|-------|
| News ingest (RSS + API) | 🟠 | Phase 6 | Fetch news from CryptoPanic, exchanges |
| Deduplication | 🟠 | Phase 6 | Hash-based, skip repeated stories |
| News Analyzer agent | 🟠 | Phase 6 | Classify: related symbol, sentiment, importance |
| News observation log | 🟠 | Phase 6 | Store for audit (does NOT affect voting yet) |

---

## 12. Backend Services

| Service | Status | Phase | Notes |
|---------|--------|-------|-------|
| `market_data_service.py` | 🟢 | 2 | Binance WebSocket + REST adapter |
| `feature_engine.py` | 🟢 | 2 | Indicator calculation, regime detection |
| `ai_orchestrator.py` | 🟢 | 3 | Agent routing, caching, triggering |
| `market_analyst_prompt.py` | 🟢 | 3 | Claude Sonnet prompt builder |
| `risk_analyzer_prompt.py` | 🟢 | 3 | Claude Haiku prompt builder |
| `decision_aggregator.py` | 🟢 | 3 | Voting + weighting logic |
| `simulate_engine.py` | 🟢 | 4 | Paper trading, order execution, P&L |
| `telegram_service.py` | 🟢 | 5 | Message formatting + delivery |
| `celery_tasks.py` | 🟢 | 2 | Scheduled market pulls, analysis triggers |

---

## 13. Frontend Pages

| Page | Route | Status | Phase | Notes |
|------|-------|--------|-------|-------|
| Login | `/login` | 🟢 | 1 | Email + password |
| Dashboard | `/` | 🟢 | 5 | Key stats, summary cards |
| Market Overview | `/market` | 🟢 | 2 | Table of symbols, prices, 24h change |
| Watchlist | `/watchlist` | 🟢 | 2 | Detailed prices for watched symbols |
| AI Analysis | `/analysis` | 🟢 | 5 | Latest signals, confidence scores |
| Trading / Orders | `/trading` | 🟢 | 5 | Order history, P&L breakdown |
| Positions | `/positions` | 🟢 | 5 | Current open positions |
| Settings / Risk | `/settings/risk` | 🟢 | 5 | Edit risk parameters |
| AI Trace | `/admin/trace` | 🟢 | 5 | Deep dive into each AI run |
| Admin Audit Log | `/admin/audit` | 🟠 | Phase 7+ | Who changed what, when |

---

## 14. Database Tables (11 Total)

| Table | Status | Phase | Rows |
|-------|--------|-------|------|
| `users` | 🟢 | 1 | ~5–10 (roles: SUPER_ADMIN, TRADER, VIEWER) |
| `symbols` | 🟢 | 2 | 5 (BTC, ETH, SOL, XRP, DOGE) |
| `market_candles` | 🟢 | 2 | ~1M+ (1m/5m/15m/1h, 5 symbols × ~1 year history) |
| `market_ticks` | 🟠 | Phase 4+ | ~100M (optional, high volume) |
| `feature_snapshots` | 🟢 | 2 | ~10k (1 per candle close, per symbol) |
| `ai_runs` | 🟢 | 3 | ~500/day (every 3–5 min) |
| `ai_agent_outputs` | 🟢 | 3 | ~1,000/day (2 agents per run) |
| `vote_results` | 🟢 | 3 | ~500/day (one per AI run) |
| `simulate_orders` | 🟢 | 4 | ~1,000/month (typical trade: 10–50/day) |
| `positions` | 🟢 | 4 | ~2 (max open, updated daily) |
| `settings` | 🟢 | 1 | ~15 (risk controls, mode, etc.) |

---

## 15. Celery Background Tasks

| Task | Status | Phase | Interval | Notes |
|------|--------|-------|----------|-------|
| `pull_market_data` | 🟢 | 2 | Every 1 min | Fetch latest candles, store to DB |
| `run_analysis_trigger` | 🟢 | 3 | Every 3 min | Check triggers, route to AI agents |
| `close_expired_positions` | 🟢 | 4 | Every 1 min | Monitor SL/TP, close if hit |
| `daily_summary` | 🟢 | 5 | Daily 07:00 AM MYT | Aggregate P&L, send Telegram |
| `sync_watchlist` (optional) | 🟠 | Phase 4+ | Every 5 min | Validate whitelist against Binance |

---

## 16. API Endpoints (30 Total)

### Authentication (4)
- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/refresh`
- `GET /api/v1/auth/me`

### Market Data (4)
- `GET /api/v1/market/tickers`
- `GET /api/v1/market/{symbol}`
- `GET /api/v1/market/{symbol}/candles?tf=5m&limit=100`
- `GET /api/v1/market/search?q=BTC`

### Watchlist (3)
- `GET /api/v1/watchlist`
- `POST /api/v1/watchlist` (add symbol)
- `DELETE /api/v1/watchlist/{symbol}`

### AI Analysis (3)
- `GET /api/v1/analysis/latest/{symbol}`
- `GET /api/v1/analysis?limit=20`
- `GET /api/v1/analysis/{id}/trace` (full AI run details)

### Trading (4)
- `GET /api/v1/trading/positions`
- `GET /api/v1/trading/orders?status=closed`
- `GET /api/v1/trading/orders/{id}`
- `GET /api/v1/trading/portfolio/summary`

### Admin (5)
- `GET /api/v1/admin/dashboard/summary`
- `GET /api/v1/admin/settings/risk`
- `PUT /api/v1/admin/settings/risk`
- `POST /api/v1/admin/system/pause`
- `POST /api/v1/admin/system/resume`

---

## 17. Integration Tests

| Test | Status | Phase | Notes |
|------|--------|-------|-------|
| E2E: Market data → Indicators → AI → Order | 🟢 | 4 | Full pipeline |
| Unit: Each indicator (EMA, RSI, ATR) | 🟢 | 2 | Verify against pandas |
| Unit: Simulate engine P&L calculation | 🟢 | 4 | Manual verification |
| Integration: Binance API connectivity | 🟢 | 2 | API key validation |
| Integration: Telegram message delivery | 🟢 | 5 | Verify send + receive |

---

## Summary

**MVP Scope:** 7 major modules across 8 phases  
**Total Features:** 95+ planned (all for MVP v1)  
**Deferred:** News voting, email, demo mode, tick storage (Phase 4+)  
**Target Completion:** 8–12 weeks (from today)

---

**End of Functional Spec — v2.0**
