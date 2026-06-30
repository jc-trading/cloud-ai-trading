# P3 Architecture Review & Implementation Plan

**Date:** 2026-04-15  
**Phase:** Planning & Code Review  
**Status:** 🔍 REVIEW IN PROGRESS  

---

## Executive Summary

P3 (Auto Position Management) will add **risk management and position sizing automation** to the trading system. The existing `portfolio.py` module provides basic position tracking; P3 extends this with:

1. **Risk-based position sizing** (Kelly criterion variants)
2. **Strict risk limit enforcement** (daily loss, max drawdown, concentration)
3. **Automatic position adjustment** (stop loss, trailing stops, profit-taking)
4. **Real-time risk metrics** (Sharpe ratio, Value at Risk, drawdown tracking)
5. **Emergency risk controls** (auto-close on loss limit, pause trading)

---

## Existing Foundation

### What's Already Implemented

✅ **Position Model** (`trading/models.py`)
- Basic position tracking (entry_price, quantity, exit_price, status)
- Position type (LONG/SHORT)
- Entry/exit dates
- Realized P&L calculation

✅ **PortfolioManager** (`trading/portfolio.py`)
- Add/close positions
- Calculate unrealized P&L
- Calculate realized P&L
- Calculate win rate and trade statistics
- Update portfolio statistics

✅ **Position Data Models**
- `Position` - Individual trades
- `PortfolioStats` - Aggregated metrics

### What's Missing for P3

❌ **Risk Management**
- No position sizing engine
- No risk limit definitions
- No validation before position entry

❌ **Dynamic Adjustment**
- No stop loss enforcement
- No take profit checking
- No trailing stop implementation
- No partial profit-taking

❌ **Risk Metrics**
- No Sharpe ratio calculation
- No Value at Risk (VaR)
- No max drawdown tracking
- No portfolio concentration metrics

❌ **API Endpoints**
- No risk management endpoints
- No position adjustment endpoints
- No risk metrics dashboard

---

## Critical Design Questions for P3

### 1. Position Sizing Algorithm

**Question:** How should position size be calculated?

**Options:**

**Option A: Kelly Criterion (Academic)**
```
f* = (win_rate - (1 - win_rate) / profit_factor) / odds
position_size = account_equity * f* * 0.25  # Use 25% of Kelly (fractional Kelly)
```
Pros: Optimal growth, proven in betting
Cons: Requires historical data, can be aggressive

**Option B: Fixed Risk Percentage (Practical)**
```
position_size = (account_equity * risk_per_trade%) / stop_loss_distance
e.g., if account=$10k, risk=2%, SL=2%, then size = 200/0.02 = $10k
```
Pros: Simple, intuitive, direct risk control
Cons: Doesn't account for signal strength

**Option C: Signal-Weighted Risk (Hybrid)** ← RECOMMENDED
```
base_size = (account_equity * risk_per_trade%) / stop_loss_distance
adjusted_size = base_size * (signal_strength / 100)
final_size = min(adjusted_size, max_position_size)
```
Pros: Uses signal strength, respects risk limits, simple
Cons: Signal strength must be calibrated (0-100)

### 2. Risk Limits Hierarchy

**Question:** How strict should portfolio-level limits be?

**Current Proposal:**
- Per-position max loss: 2% of account
- Per-position max size: 5% of portfolio
- Portfolio max concentration: 30% (single asset)
- Portfolio max drawdown: 10% from peak
- Daily loss limit: 3%
- Max consecutive losses: 3

**Trade-off:**
- Too strict: Miss opportunities, underutilize capital
- Too loose: Risk catastrophic loss

**Recommendation:** Start with above limits, adjust after 100 trades

### 3. Stop Loss & Take Profit Management

**Question:** How to handle SL/TP in crypto markets with high volatility?

**Issues:**
- Wide stop losses needed (2-3% min) due to volatility
- Wide take profits (5-10%+) needed to reach target
- Market gaps can skip over SL during low liquidity
- Limit orders may not fill, market orders have slippage

**Approach:**
1. Use percentage-based SL/TP from entry price
2. For high-volatility assets, widen SL slightly
3. Implement partial profit-taking (close 25% at TP1, 50% at TP2, etc.)
4. Use Telegram alerts before hitting SL (price within 50 bps)

### 4. Real-Time vs Batch Calculations

**Question:** Should position adjustments be real-time or batched?

**Real-time (every price tick):**
- Pros: Immediate SL/TP execution, no delay
- Cons: More database writes, higher CPU

**Batch (every 1 minute via Celery):**
- Pros: Efficient, aligns with signal generation
- Cons: 1-minute delay in SL execution

**Recommendation:** Batch at 1 minute interval (matches signal generation frequency)

### 5. Historical Data for Risk Metrics

**Question:** How much historical data needed for Sharpe ratio, VaR, etc?

**Typical requirements:**
- Sharpe ratio: 30+ trades minimum
- VaR (95%): 20+ trades minimum
- Correlation: 50+ price points

**Implementation:**
- Show metrics as "updating..." if insufficient data
- Start with simple metrics, add complex ones when data available
- Store daily snapshots for long-term trends

---

## Implementation Risk Assessment

### 🔴 HIGH RISK

**1. Position Sizing Miscalculation**
- Risk: Over/undersized positions lose effective risk control
- Mitigation: Extensive unit tests, compare against reference calculations
- Test: Run 1000 position sizing scenarios, verify limits always respected

**2. SL/TP Enforcement Gaps**
- Risk: Positions don't close when they should, losses exceed intended
- Mitigation: Rigorous integration tests with historical OHLCV data
- Test: Replay past month of price data, verify every SL/TP hit is caught

**3. Portfolio Limit Bypass**
- Risk: Code paths that allow position entry despite exceeded limits
- Mitigation: Check limits BEFORE position entry, not after
- Test: Attempt to create position that violates each limit, verify rejection

### 🟡 MEDIUM RISK

**4. Database Performance**
- Risk: Real-time updates for 10+ positions, 1000+ metrics/day
- Mitigation: Proper indexing, batch writes where possible
- Test: Load test with 1000 concurrent positions, measure latency

**5. Sharpe/VaR Calculation Correctness**
- Risk: Financial formulas implemented wrong, metrics misleading
- Mitigation: Compare against NumPy/pandas calculations, validate formulas
- Test: Known datasets with pre-calculated results

**6. Timezone Handling**
- Risk: Position entry/exit times recorded in wrong timezone
- Mitigation: Always use UTC internally, convert on display
- Test: Verify all timestamps in database are UTC

### 🟢 LOW RISK

**7. API Rate Limiting**
- Risk: Risk metrics endpoint called too frequently
- Mitigation: Cache results, return 304 if unchanged
- Test: Load test API with rapid requests

---

## Proposed Implementation Order

### Phase 3A: Core Risk Engine (Week 1)

**Files to create:**
- `backend/app/modules/risk/__init__.py`
- `backend/app/modules/risk/models.py` - RiskLimit, PositionMetric models
- `backend/app/modules/risk/engine.py` - RiskEngine class
- `backend/app/modules/risk/validators.py` - Input validation
- `tests/test_p3_risk_engine.py` - Unit tests

**Work:**
- [ ] Design RiskLimit database model
- [ ] Implement RiskEngine.calculate_position_size()
- [ ] Implement RiskEngine.validate_new_position()
- [ ] Write comprehensive unit tests
- [ ] Verify position sizing respects all limits

**Success:** Position sizing works correctly, all limits enforced

---

### Phase 3B: Real-Time Tracking (Week 2)

**Files to create:**
- `backend/app/modules/risk/tracker.py` - PortfolioRiskTracker class
- `backend/app/tasks/risk_tasks.py` - Celery tasks
- Database migrations for position_metrics, portfolio_risk_metrics

**Work:**
- [ ] Implement PortfolioRiskTracker metrics calculation
- [ ] Create Celery task: monitor_portfolio() - every 1 minute
- [ ] Store position-level metrics (P&L, MAE, MFE)
- [ ] Store portfolio-level metrics (drawdown, concentration)
- [ ] Integration tests with historical price data

**Success:** Metrics update in real-time, database stores correctly

---

### Phase 3C: Position Adjustment (Week 3)

**Files to create:**
- `backend/app/modules/risk/adjuster.py` - PositionAdjuster class
- Extend `risk_tasks.py` with adjustment logic
- `tests/test_p3_adjustment.py` - Adjustment tests

**Work:**
- [ ] Implement PositionAdjuster.check_and_adjust_positions()
- [ ] Implement SL/TP hit detection
- [ ] Implement trailing stop logic
- [ ] Implement profit-taking logic
- [ ] Implement portfolio rebalancing
- [ ] Implement emergency risk controls
- [ ] Integration tests with extreme scenarios

**Success:** Positions close/adjust automatically, edge cases handled

---

### Phase 3D: API & Dashboard (Week 4)

**Files to create:**
- `backend/app/api/routes/positions.py` - Position CRUD endpoints
- `backend/app/api/routes/risk_management.py` - Risk metrics endpoints

**Work:**
- [ ] Position CRUD: GET/POST/PATCH/DELETE /api/positions
- [ ] Risk limits: GET/PATCH /api/risk/limits
- [ ] Portfolio metrics: GET /api/portfolio/risk-metrics
- [ ] Portfolio analysis: GET /api/portfolio/{concentration|correlation|drawdown}
- [ ] Full integration tests
- [ ] API documentation

**Success:** All endpoints working, dashboard can display real-time data

---

## Code Quality Checklist

For each implementation phase:

- [ ] Type hints complete (100% coverage)
- [ ] Docstrings on all public methods
- [ ] Unit tests (>90% coverage)
- [ ] Integration tests with realistic data
- [ ] Error handling for edge cases
- [ ] Logging appropriate (info + debug levels)
- [ ] No hardcoded values (use config)
- [ ] Decimal used for all financial math
- [ ] Async/await patterns consistent
- [ ] SQLAlchemy ORM used correctly
- [ ] No N+1 query problems
- [ ] Database indexes added where needed

---

## Expected Challenges

### 1. Position Sizing Coordination with Strategy Engine
- **Current:** P2 calculates position sizes from strategy weights
- **P3 Adds:** Risk-based position sizing
- **Solution:** P3 position size = min(P2 recommended, P3 risk-based)

### 2. Historical Backtesting Data
- **Problem:** Need OHLCV candles for SL/TP testing
- **Solution:** Leverage existing OHLCVCandle model, stored in DB

### 3. Metric Calculation Performance
- **Problem:** Calculating Sharpe ratio, VaR for every position every minute
- **Solution:** Cache results, calculate only when needed, batch updates

### 4. Database Schema Evolution
- **Problem:** Adding new risk-related tables
- **Solution:** Use Alembic migrations, maintain backward compatibility

---

## Testing Strategy Summary

### Unit Tests
- Position sizing: 50+ test cases
- Risk validation: 30+ test cases
- Metric calculations: 20+ test cases
- Total: 100+ unit tests

### Integration Tests
- Full position entry-to-exit: 10+ scenarios
- Risk limit enforcement: 15+ scenarios
- Position adjustment: 20+ scenarios
- Emergency conditions: 10+ scenarios
- Total: 55+ integration tests

### Load Tests
- 1000 concurrent positions
- 100 real-time metric updates/second
- API response time <100ms

### Regression Tests
- Verify existing P0, P1, P2 still work
- Portfolio manager still calculates correctly
- Database consistency maintained

---

## Dependencies on Other Phases

**Depends on:**
- ✅ P0: Claude AI signal generation (uses signals for position entry)
- ✅ P1: Extended signals (MACD, BB)
- ✅ P2: QuantStrategy (uses strategy-based position sizing)

**Impacts:**
- P4: Live trading execution (uses P3 position sizing & risk limits)

---

## Success Metrics

By end of P3:

- ✅ Position sizing respects all risk limits
- ✅ Portfolio never exceeds 10% max drawdown
- ✅ SL/TP executed automatically within 1 minute
- ✅ Risk metrics updated in real-time
- ✅ Emergency conditions trigger alerts
- ✅ 100+ unit tests + 55+ integration tests all passing
- ✅ Performance: <100ms response time for risk endpoints
- ✅ Code coverage: >90%

---

## Recommendations

1. **Start with Design Review** - Codex can review the P3 spec and models
2. **Build Incrementally** - Each 1-week phase is independent
3. **Test Thoroughly** - Risk management is critical, over-test rather than under-test
4. **Integrate with P2** - Coordinate position sizing between P2 and P3
5. **Monitor Performance** - Track position sizing distribution, hit rates, metrics latency

---

**Status:** Ready for detailed code review and implementation planning.

**Next Step:** Schedule P3 code review, begin Phase 3A implementation.
