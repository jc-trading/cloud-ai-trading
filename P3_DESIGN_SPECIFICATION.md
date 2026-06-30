# P3 Auto Position Management - Design Specification

**Phase:** P3 (Phase 5)  
**Status:** 📋 PLANNING  
**Created:** 2026-04-15  
**Target Completion:** TBD

---

## 🎯 Overview

P3 implements **Auto Position Management and Risk Control** - the automated system that:
- Calculates optimal position sizes based on risk parameters
- Enforces strict risk limits (max loss per position, portfolio loss, concentration)
- Manages stop losses and take profits automatically
- Dynamically adjusts positions based on changing portfolio risk
- Tracks and reports portfolio risk metrics in real-time

---

## 📋 Core Requirements

### 1. Position Sizing Engine

**Purpose:** Calculate position size from trading signal strength and risk parameters

**Inputs:**
- Signal strength (0-100)
- Available capital
- Risk level (low/medium/high)
- Account equity
- Max risk per trade

**Calculation Formula:**
```
position_size = min(
  (account_equity * risk_per_trade / stop_loss_distance) * signal_strength_multiplier,
  max_position_size
)
```

**Constraints:**
- Max position size per symbol: 5% of portfolio
- Min position size: $10 (or equivalent in crypto)
- Single position max loss: 2% of account
- No position if signal strength < 50

### 2. Risk Limits Enforcement

**Per-Position Limits:**
- Max loss per trade: 2% of account equity
- Max position size: 5% of portfolio
- Min confidence threshold: 60 (adjusted by risk level)

**Portfolio-Level Limits:**
- Max portfolio loss (peak drawdown): 10% of max equity
- Max concentration: 30% in single asset
- Max open positions: 10
- Max correlated positions: 3 (crypto with >0.7 correlation)

**Time-Based Limits:**
- Max position age without profit: 7 days
- Max consecutive losses: 3 positions
- Daily loss limit: 3% (auto-close if breached)

### 3. Position Tracking

**Track for each position:**
- Entry price, quantity, entry time
- Stop loss level and distance
- Take profit level
- Max favorable excursion (MFE) - highest price reached
- Max adverse excursion (MAE) - lowest price reached
- Current P&L (realized + unrealized)
- Win/loss status upon close
- Hold time
- Exit reason (TP, SL, signal reversal, manual close, time-based)

### 4. Dynamic Position Adjustment

**Scenarios triggering adjustment:**
1. **Trailing Stop** - Move SL higher as price moves up
2. **Partial Profit-Taking** - Close 25% at first TP level
3. **Loss Mitigation** - Close if position underwater > 24 hours
4. **Portfolio Rebalance** - Reduce size if concentration exceeds limit
5. **Risk Escalation** - Close on 3rd consecutive loss

**Adjustment Logic:**
```
if current_portfolio_drawdown > 8%:
  close_positions_with_negative_pnl()
  reduce_position_sizes_by_50()

if position_age > 7_days and position_pnl < 0:
  close_position()

if portfolio_concentration > 30%:
  reduce_largest_position()
```

### 5. Portfolio Risk Metrics

**Real-time Tracking:**
- **Unrealized P&L** - Current open position profit/loss
- **Realized P&L** - Closed position profit/loss
- **Win Rate** - % of winning trades
- **Profit Factor** - Total wins / Total losses
- **Max Drawdown** - Largest peak-to-trough decline
- **Sharpe Ratio** - Return per unit of risk (updated daily)
- **Value at Risk (VaR)** - Max loss at 95% confidence
- **Portfolio Beta** - Correlation to BTC (market)
- **Concentration Risk** - Largest position % of portfolio

### 6. API Endpoints for Risk Management

**Position Management:**
- `GET /api/positions` - List all open positions
- `POST /api/positions` - Create new position
- `PATCH /api/positions/{id}` - Update position (SL/TP)
- `DELETE /api/positions/{id}` - Close position
- `GET /api/positions/{id}/metrics` - Position P&L metrics

**Risk Limits:**
- `GET /api/risk/limits` - Current limits
- `PATCH /api/risk/limits` - Update limits
- `GET /api/risk/limits/check` - Check if new trade allowed

**Portfolio Analysis:**
- `GET /api/portfolio/risk-metrics` - All risk metrics
- `GET /api/portfolio/concentration` - Asset concentration
- `GET /api/portfolio/correlation` - Position correlation
- `GET /api/portfolio/drawdown` - Max drawdown tracking

### 7. Risk Dashboard Data

**Real-time Display:**
- Portfolio equity curve
- Current drawdown %
- Win rate %
- Profit factor
- Largest open position
- Days-in-trade (oldest position)
- Risk/reward ratio next trade
- Portfolio concentration pie chart

---

## 🏗️ Architecture

### File Structure

```
backend/app/modules/risk/
├── __init__.py
├── models.py          # RiskLimit, PositionMetric, DrawdownRecord
├── engine.py          # RiskEngine - position sizing & limit checking
├── tracker.py         # PortfolioRiskTracker - real-time metrics
├── adjuster.py        # PositionAdjuster - dynamic adjustments
└── validators.py      # RiskValidator - input validation

backend/app/api/routes/
├── positions.py       # Position CRUD endpoints
└── risk_management.py # Risk limits & metrics endpoints

backend/app/tasks/
└── risk_tasks.py      # Celery: Check limits, adjust positions, update metrics
```

### Key Classes

**RiskEngine:**
```python
class RiskEngine:
    def calculate_position_size(
        account_equity: Decimal,
        signal_strength: float,
        risk_level: str,
        current_portfolio_data: Dict,
    ) -> PositionSizeRecommendation
    
    def validate_new_position(
        symbol: str,
        quantity: Decimal,
        current_limits: RiskLimits,
        portfolio: PortfolioSnapshot,
    ) -> Tuple[bool, str]  # (is_allowed, reason)
```

**PortfolioRiskTracker:**
```python
class PortfolioRiskTracker:
    async def update_metrics(
        portfolio: Portfolio,
        current_prices: Dict[str, Decimal],
    ) -> PortfolioMetrics
    
    async def calculate_var(
        historical_returns: List[Decimal],
        confidence_level: float = 0.95,
    ) -> Decimal
    
    async def calculate_sharpe_ratio(
        returns: List[Decimal],
        risk_free_rate: float = 0.0001,
    ) -> float
```

**PositionAdjuster:**
```python
class PositionAdjuster:
    async def check_and_adjust_positions(
        positions: List[Position],
        current_prices: Dict[str, Decimal],
        risk_limits: RiskLimits,
    ) -> List[AdjustmentAction]
    # Returns actions: close, reduce_size, move_sl, etc.
```

---

## 🔄 Workflows

### Position Entry Workflow

```
TradingSignal Generated
  ↓
RiskEngine.calculate_position_size()
  ├─ Get signal strength
  ├─ Get account equity
  ├─ Get current positions
  ├─ Calculate optimal size
  └─ Return PositionSizeRecommendation
  ↓
RiskEngine.validate_new_position()
  ├─ Check per-position limits
  ├─ Check portfolio concentration
  ├─ Check max open positions
  ├─ Check daily loss limit
  └─ Return bool + reason
  ↓
IF allowed:
  PortfolioManager.add_position()
    ├─ Calculate SL/TP levels
    ├─ Store position with risk metadata
    └─ Log position entry
  ↓
  Send notification
    ├─ Telegram: New position
    └─ Dashboard: Update portfolio
ELSE:
  Log rejection reason
  Update rejected_signals table
```

### Real-Time Risk Monitoring (Every 1 minute)

```
Celery Beat Task: risk_tasks.monitor_portfolio()
  ↓
PortfolioRiskTracker.update_metrics()
  ├─ Fetch current prices
  ├─ Calculate unrealized P&L per position
  ├─ Calculate portfolio metrics
  └─ Store metrics in DB (time-series)
  ↓
PositionAdjuster.check_and_adjust_positions()
  ├─ Check stop loss hits
  ├─ Check trailing stop updates
  ├─ Check profit-taking triggers
  ├─ Check time-based exits
  └─ Check portfolio-level adjustments
  ↓
For each adjustment:
  PositionAdjuster.apply_adjustment()
    ├─ Close position (if SL/TP hit)
    ├─ Move stop loss (if trailing)
    ├─ Reduce size (if concentration high)
    └─ Send notification
  ↓
PortfolioRiskTracker.check_portfolio_limits()
  ├─ Check daily loss limit
  ├─ Check max drawdown
  ├─ Check consecutive losses
  └─ Trigger emergency actions if breached
```

### Emergency Risk Management

```
IF daily_loss > 3% of equity:
  Close all non-profitable positions
  Reduce remaining sizes by 50%
  Alert user: "Daily loss limit reached"
  
IF max_drawdown > 10% from peak:
  Close all positions in losing state
  Pause new trades for 24 hours
  Alert user: "Portfolio in drawdown"
  
IF consecutive_losses >= 3:
  Pause new trades until win
  Reduce position sizes by 25%
  Alert user: "Consecutive losses detected"
```

---

## 📊 Database Schema Extensions

### New Tables

**risk_limits:**
```sql
CREATE TABLE risk_limits (
  id UUID PRIMARY KEY,
  watchlist_id UUID NOT NULL,
  max_position_size_percent DECIMAL(5,2) DEFAULT 5.0,
  max_portfolio_loss_percent DECIMAL(5,2) DEFAULT 10.0,
  max_loss_per_trade_percent DECIMAL(5,2) DEFAULT 2.0,
  daily_loss_limit_percent DECIMAL(5,2) DEFAULT 3.0,
  max_open_positions INT DEFAULT 10,
  max_concentration_percent DECIMAL(5,2) DEFAULT 30.0,
  min_win_rate_percent DECIMAL(5,2) DEFAULT 40.0,
  created_at TIMESTAMP,
  updated_at TIMESTAMP
);
```

**position_metrics:**
```sql
CREATE TABLE position_metrics (
  id UUID PRIMARY KEY,
  position_id UUID NOT NULL,
  current_pnl DECIMAL(20,8),
  pnl_percent DECIMAL(8,4),
  max_favorable_excursion DECIMAL(20,8),
  max_adverse_excursion DECIMAL(20,8),
  current_price DECIMAL(20,8),
  days_in_trade INT,
  recorded_at TIMESTAMP,
  FOREIGN KEY (position_id) REFERENCES positions(id)
);
```

**portfolio_risk_metrics:**
```sql
CREATE TABLE portfolio_risk_metrics (
  id UUID PRIMARY KEY,
  watchlist_id UUID NOT NULL,
  recorded_at TIMESTAMP,
  unrealized_pnl DECIMAL(20,8),
  realized_pnl DECIMAL(20,8),
  total_pnl DECIMAL(20,8),
  max_drawdown_percent DECIMAL(8,4),
  current_drawdown_percent DECIMAL(8,4),
  sharpe_ratio DECIMAL(8,4),
  win_rate_percent DECIMAL(8,4),
  profit_factor DECIMAL(8,4),
  portfolio_concentration_percent DECIMAL(8,4),
  var_95 DECIMAL(20,8),
  FOREIGN KEY (watchlist_id) REFERENCES watchlists(id)
);
```

---

## 🧪 Testing Strategy

### Unit Tests
- Position sizing calculations
- Risk limit validation
- Metric calculations (Sharpe, VaR, etc.)
- Position adjustment logic

### Integration Tests
- Full position entry → adjustment → exit workflow
- Risk limit enforcement with multiple positions
- Emergency risk management trigger
- Real-time metric updates

### Stress Tests
- Portfolio under extreme drawdown
- Rapid price movements (SL/TP hits)
- Multiple positions exiting simultaneously
- Max concentration scenarios

---

## 🔄 Celery Tasks

**New tasks needed:**

1. `monitor_portfolio()` - Every minute
   - Update real-time metrics
   - Check and apply adjustments
   - Check portfolio limits

2. `update_risk_metrics()` - Every hour
   - Calculate Sharpe ratio
   - Calculate VaR
   - Update correlation matrix
   - Archive metrics

3. `check_emergency_conditions()` - Every minute
   - Check daily loss
   - Check max drawdown
   - Check consecutive losses
   - Trigger alerts

---

## ⚠️ Risk Considerations

1. **Order Slippage** - Actual execution price may differ from SL/TP
2. **Gap Risk** - Price may gap past SL during low liquidity
3. **Leverage Risk** - Not applicable for spot trading, but important if margin added
4. **Correlation Risk** - Multiple positions may move together
5. **Execution Risk** - Unable to close position when needed
6. **Model Risk** - Risk calculations assume normal distribution (not true for crypto)

---

## 🚀 Implementation Phases

### Phase 3A: Core Risk Engine (Week 1)
- [  ] RiskEngine position sizing
- [  ] Risk limit validation
- [  ] Database schema
- [  ] Basic unit tests

### Phase 3B: Position Tracking (Week 2)
- [  ] Position metrics calculation
- [  ] PortfolioRiskTracker
- [  ] Real-time updates
- [  ] Celery tasks

### Phase 3C: Dynamic Adjustment (Week 3)
- [  ] PositionAdjuster implementation
- [  ] SL/TP enforcement
- [  ] Partial profit-taking
- [  ] Portfolio rebalancing

### Phase 3D: Advanced Metrics & Dashboard (Week 4)
- [  ] Sharpe ratio calculation
- [  ] Value at Risk (VaR)
- [  ] Correlation analysis
- [  ] Risk dashboard API endpoints
- [  ] Integration tests

---

## 📝 Success Criteria

✅ All positions sized within risk limits  
✅ Stop losses enforced automatically  
✅ Portfolio loss never exceeds 10% max drawdown  
✅ Real-time risk metrics updated every minute  
✅ Emergency conditions trigger alerts  
✅ Full test coverage (>90%)  
✅ Integration tests pass with live price data  

---

## 📚 References

- [Kelly Criterion](https://en.wikipedia.org/wiki/Kelly_criterion) - Optimal position sizing
- [Sharpe Ratio](https://www.investopedia.com/terms/s/sharperatio.asp) - Risk-adjusted return
- [Value at Risk](https://www.investopedia.com/terms/v/var.asp) - Maximum loss at confidence level
- [Maximum Drawdown](https://www.investopedia.com/terms/m/maximum-drawdown-mdd.asp) - Peak-to-trough decline

---

**Next:** Review this spec with team, refine requirements, begin implementation.
