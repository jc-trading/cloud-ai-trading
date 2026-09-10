# Cloud AI Trading — Development Progress

> **Purpose:** Development tracker for tasks, phases, and implementation status.  
> **Current Status:** Fresh start (MVPv1 redesign).  
> **Last Updated:** April 12, 2026  
> For feature status, see `FUNCTIONAL_SPEC.md`. For architecture, see `CloudAiTrading-System-Plan.md`.

---

## 📝 Development Phases (8 Total)

### Phase 1: Core Infrastructure ⏳ NEXT

**Goal:** Get Docker + FastAPI + PostgreSQL + auth working.

| # | Task | Effort | Status | Notes |
|---|------|--------|--------|-------|
| 1.1 | Docker Compose (5 services) | 4h | ⏳ Todo | Setup backend, celery-worker, celery-beat, postgres, redis |
| 1.2 | FastAPI project skeleton | 2h | ⏳ Todo | app/main.py, routers, config, dependencies |
| 1.3 | PostgreSQL + Alembic migrations | 3h | ⏳ Todo | Database initialization, migration structure |
| 1.4 | JWT auth (python-jose + bcrypt) | 2h | ⏳ Todo | Token generation, refresh, verification |
| 1.5 | RBAC (3 roles: SUPER_ADMIN, TRADER, VIEWER) | 2h | ⏳ Todo | Permission gates on endpoints |
| 1.6 | User registration + login | 3h | ⏳ Todo | Endpoints + database schema |
| 1.7 | Test Docker + login flow | 1h | ⏳ Todo | Verify all services start, JWT works |

**Phase 1 Duration:** ~17 hours  
**Estimated Completion:** Week 1

---

### Phase 2: Market Data & Features ⏳ AFTER PHASE 1

**Goal:** Real-time Binance data + technical indicators.

| # | Task | Effort | Status | Notes |
|---|------|--------|--------|-------|
| 2.1 | Binance CCXT adapter (market data service) | 4h | ⏳ Todo | WebSocket ticker + trade, REST candles |
| 2.2 | Market candle storage (market_candles table) | 2h | ⏳ Todo | Schema + OHLCV insert logic |
| 2.3 | Technical indicators (EMA, RSI, ATR, BB, MACD) | 5h | ⏳ Todo | Pure pandas, no TA-Lib dependency |
| 2.4 | Feature engine (aggregate summary JSON) | 3h | ⏳ Todo | Combined indicator snapshot per symbol+timeframe |
| 2.5 | Watchlist management | 2h | ⏳ Todo | CRUD endpoints, seed default 5 symbols |
| 2.6 | Market data Celery task (every 1m) | 2h | ⏳ Todo | pull_market_data, store candles |
| 2.7 | Market overview frontend (simple table) | 3h | ⏳ Todo | Symbol list, prices, 24h change |
| 2.8 | Watchlist UI (add/remove, prices) | 2h | ⏳ Todo | Vue page, wire to API |

**Phase 2 Duration:** ~23 hours  
**Estimated Completion:** Week 2–3

---

### Phase 3: AI Orchestrator (2 Agents) ⏳ AFTER PHASE 2

**Goal:** Claude Sonnet + Haiku agents, voting, triggering.

| # | Task | Effort | Status | Notes |
|---|------|--------|--------|-------|
| 3.1 | Anthropic SDK integration | 1h | ⏳ Todo | Sync client, async client setup |
| 3.2 | Market Analyst agent (Sonnet) | 4h | ⏳ Todo | Prompt builder, JSON parser, output storage |
| 3.3 | Risk Analyzer agent (Haiku) | 3h | ⏳ Todo | Veto logic, risk scoring |
| 3.4 | Trigger engine (strict conditions) | 3h | ⏳ Todo | Spike/breakout/volatility detection |
| 3.5 | Result caching (10-min window) | 2h | ⏳ Todo | Redis key design, TTL logic |
| 3.6 | Vote aggregation (60/40 weighting) | 2h | ⏳ Todo | Score calculation, decision maker |
| 3.7 | AI run logging (ai_runs + ai_agent_outputs) | 2h | ⏳ Todo | Full trace storage in DB |
| 3.8 | Celery task: run_analysis_trigger (every 3m) | 2h | ⏳ Todo | Orchestrate agent calls |
| 3.9 | Token usage tracking (log input/output tokens) | 1h | ⏳ Todo | Monitor cost per run |

**Phase 3 Duration:** ~20 hours  
**Estimated Completion:** Week 4–5

---

### Phase 4: Simulate Engine ⏳ AFTER PHASE 3

**Goal:** Paper trading with realistic P&L.

| # | Task | Effort | Status | Notes |
|---|------|--------|--------|-------|
| 4.1 | Simulate order placement | 2h | ⏳ Todo | Receive trade intent, create simulate_order |
| 4.2 | Position tracking (opened → monitoring → closed) | 2h | ⏳ Todo | Status flow, position model |
| 4.3 | Monitor SL/TP (every 1m Celery task) | 3h | ⏳ Todo | Check close_expired_positions |
| 4.4 | P&L calculation (gross + net) | 2h | ⏳ Todo | Include slippage (5bps) + fees (10bps) |
| 4.5 | Daily P&L aggregation | 1h | ⏳ Todo | Sum closed trades, unrealized |
| 4.6 | Order history API + frontend | 2h | ⏳ Todo | List, filter, detail view |
| 4.7 | Risk guard: max 2 positions | 1h | ⏳ Todo | Hard blocker in decision aggregator |
| 4.8 | Risk guard: daily loss limit (3%) | 1h | ⏳ Todo | Hard blocker, check daily P&L |
| 4.9 | Manual pause/resume system | 1h | ⏳ Todo | Admin toggle, block AI when paused |

**Phase 4 Duration:** ~15 hours  
**Estimated Completion:** Week 6

---

### Phase 5: Telegram & Admin Dashboard ⏳ AFTER PHASE 4

**Goal:** Notifications + audit UI.

| # | Task | Effort | Status | Notes |
|---|------|--------|--------|-------|
| 5.1 | Telegram bot setup (token + chat ID) | 1h | ⏳ Todo | Message sending service |
| 5.2 | Telegram event handlers (5 types) | 3h | ⏳ Todo | Opportunity, Risk Blocked, Opened, Closed, Daily Summary |
| 5.3 | Message formatting + delivery queue | 2h | ⏳ Todo | Celery task for reliable send |
| 5.4 | Admin dashboard backend (summary stats) | 2h | ⏳ Todo | /admin/dashboard endpoint |
| 5.5 | AI Trace viewer backend | 2h | ⏳ Todo | /analysis/{id}/trace with full detail |
| 5.6 | Dashboard frontend (summary cards) | 3h | ⏳ Todo | Vue page showing key metrics |
| 5.7 | AI Trace viewer frontend | 3h | ⏳ Todo | Deep dive into each AI run |
| 5.8 | Risk settings UI (edit controls) | 2h | ⏳ Todo | Pause/resume, whitelist edit |
| 5.9 | Orders table UI (history) | 2h | ⏳ Todo | Filter, sort, detail view |
| 5.10 | Positions monitor UI | 1h | ⏳ Todo | Current open, unrealized P&L |

**Phase 5 Duration:** ~21 hours  
**Estimated Completion:** Week 7–8

---

### Phase 6: News Observation (Optional) ⏳ AFTER PHASE 5

**Goal:** News ingest + logging (non-voting).

| # | Task | Effort | Status | Notes |
|---|------|--------|--------|-------|
| 6.1 | News ingest service (RSS + APIs) | 3h | ⏳ Todo | CryptoPanic, Binance RSS sources |
| 6.2 | News deduplication (hash-based) | 1h | ⏳ Todo | Avoid duplicate processing |
| 6.3 | News Analyzer agent (Haiku) | 2h | ⏳ Todo | Symbol relevance, sentiment, importance |
| 6.4 | News observation logging (no voting impact) | 1h | ⏳ Todo | Store in DB for audit |
| 6.5 | News observation UI page | 2h | ⏳ Todo | List, filter, sentiment tags |

**Phase 6 Duration:** ~9 hours  
**Estimated Completion:** Week 9 (optional, can skip)

---

### Phase 7: Local 24-Hour Stability ⏳ AFTER PHASE 6

**Goal:** Graceful startup, shutdown, offline compensation.

| # | Task | Effort | Status | Notes |
|---|------|--------|--------|-------|
| 7.1 | Startup compensation (pull missing candles) | 3h | ⏳ Todo | On system boot, fetch last N days |
| 7.2 | Indicator rebuild on startup | 1h | ⏳ Todo | Recompute all features from candles |
| 7.3 | Offline gap marking | 1h | ⏳ Todo | Flag periods when system was offline |
| 7.4 | Graceful shutdown (save state) | 1h | ⏳ Todo | Flush pending tasks, close connections |
| 7.5 | Session logging (timeline) | 1h | ⏳ Todo | When system started/stopped |
| 7.6 | Daily summary report (CSV/PDF export) | 2h | ⏳ Todo | P&L, trades, signals, blocks |
| 7.7 | Stability testing (7-day run) | 8h | ⏳ Todo | Run locally 6h/day, verify no crashes |

**Phase 7 Duration:** ~17 hours  
**Estimated Completion:** Week 10–11

---

### Phase 8: Cloud Migration ⏳ AFTER PHASE 7

**Goal:** Deploy to Vultr Tokyo, 24/7 operation.

| # | Task | Effort | Status | Notes |
|---|------|--------|--------|-------|
| 8.1 | VPS provisioning (Vultr Tokyo) | 1h | ⏳ Todo | Ubuntu 22.04, Docker, Docker Compose |
| 8.2 | Database backup + recovery (PostgreSQL) | 1h | ⏳ Todo | Backup strategy, restore testing |
| 8.3 | SSL/TLS setup (Let's Encrypt) | 1h | ⏳ Todo | Certbot, auto-renewal |
| 8.4 | Nginx configuration (reverse proxy) | 1h | ⏳ Todo | Frontend static + API proxy |
| 8.5 | Deploy to VPS (git + docker compose up) | 1h | ⏳ Todo | Production .env, migrations |
| 8.6 | 24/7 monitoring setup (Grafana + Prometheus) | 3h | ⏳ Todo | CPU, memory, API response time |
| 8.7 | Log aggregation (optional: ELK stack) | 2h | ⏳ Todo | Centralized logging for debugging |
| 8.8 | Validation testing (24-hour stable run) | 8h | ⏳ Todo | Continuous operation, no crashes |

**Phase 8 Duration:** ~18 hours  
**Estimated Completion:** Week 12+

---

## 📊 Effort Summary

| Phase | Hours | Sprint |
|-------|-------|--------|
| Phase 1 | 17h | Week 1 |
| Phase 2 | 23h | Week 2–3 |
| Phase 3 | 20h | Week 4–5 |
| Phase 4 | 15h | Week 6 |
| Phase 5 | 21h | Week 7–8 |
| Phase 6 | 9h | Week 9 (optional) |
| Phase 7 | 17h | Week 10–11 |
| Phase 8 | 18h | Week 12+ |
| **Total** | **140h** | **~12 weeks (with Phase 6)** |

---

## 🎯 Current Focus

**Phase 1: Core Infrastructure** ← START HERE

### To-Do Before Phase 2:

1. ✅ Define system architecture ← DONE (in CloudAiTrading-System-Plan.md)
2. ✅ Define functional spec ← DONE (in FUNCTIONAL_SPEC.md)
3. ✅ Define development phases ← DONE (THIS FILE)
4. ⏳ Create project directory structure
5. ⏳ Set up Docker Compose (5 services)
6. ⏳ Create PostgreSQL schema (users, symbols, market_candles, etc.)
7. ⏳ Implement JWT auth (register + login)
8. ⏳ Test local setup (docker compose up, login via Swagger)

---

## 🔑 Key User Information Needed

| Item | Status | For Phase | Notes |
|------|--------|-----------|-------|
| Binance API Key + Secret | ⏳ Waiting | Phase 2 | Create at https://www.binance.com/en/account/api-management |
| Telegram Bot Token | ⏳ Waiting | Phase 5 | Create via @BotFather, get chat ID |
| Anthropic API Key | ✅ Provided | Phase 3 | Already in .env |
| Vultr Tokyo VPS (IP) | ⏳ Waiting | Phase 8 | For production deployment (not urgent) |
| Domain name (optional) | ⏳ Waiting | Phase 8 | For HTTPS (optional, can use IP) |

---

## 📈 Success Criteria per Phase

### Phase 1 ✅
- [ ] Docker services all running (`docker compose ps` shows 5 healthy)
- [ ] PostgreSQL initialized, tables created
- [ ] JWT auth working (login returns token)
- [ ] Swagger docs accessible at http://localhost:8000/api/docs

### Phase 2 ✅
- [ ] Binance WebSocket connected, prices flowing
- [ ] Market candles stored in DB (verify via pgAdmin)
- [ ] Indicators calculate correctly (EMA, RSI, ATR, BB match TradingView)
- [ ] Watchlist page shows live prices

### Phase 3 ✅
- [ ] Claude API calls successful (Sonnet + Haiku)
- [ ] AI agents output valid JSON every 3 minutes
- [ ] Voting aggregates correctly (60/40 weighting)
- [ ] AI trace shows complete decision tree

### Phase 4 ✅
- [ ] Simulate orders open + close correctly
- [ ] P&L calculation matches manual verification (with slippage + fees)
- [ ] Risk guards enforced (2-pos max, 3% daily loss)
- [ ] Order history queryable and correct

### Phase 5 ✅
- [ ] Telegram messages deliver without error
- [ ] Dashboard shows correct summary stats
- [ ] AI Trace viewer renders full run details
- [ ] Risk settings UI functional

### Phase 6 (Optional) ✅
- [ ] News ingest working, no duplicates
- [ ] News Analyzer runs, marks sentiment correctly
- [ ] News observation page displays news items

### Phase 7 ✅
- [ ] System runs 6 hours/day for 7 days without crashes
- [ ] Startup compensation pulls missing data correctly
- [ ] Daily reports generate and export cleanly

### Phase 8 ✅
- [ ] VPS deployment successful
- [ ] 24-hour test run: no crashes, all endpoints responsive
- [ ] Monitoring alerts firing correctly

---

## 🚀 Launch Readiness Checklist

Before going live (after Phase 7, before Phase 8):

- [ ] 100+ hours of stable simulate operation
- [ ] P&L tracking verified (manual spot-checks)
- [ ] Telegram delivery reliable (zero missed alerts)
- [ ] Dashboard + AI Trace feature-complete
- [ ] Monthly token spend validated < $10 USD
- [ ] Disaster recovery tested (DB backup restore)
- [ ] Risk controls hard-tested (attempt to breach, verify blocks)

---

## 💡 Notes for Developers

1. **Always code in phases** — Don't skip ahead. Each phase builds on the previous.
2. **Test as you go** — Unit tests for indicators, integration tests for API flows.
3. **Keep token cost in mind** — Cache aggressively, trigger selectively, use Haiku for simple checks.
4. **Audit trail always** — Log every decision, every API call. Future you will thank you.
5. **Stability > Features** — Get 80% working, stabilized for a week, before polishing the last 20%.

---

**End of Progress — v2.0**
