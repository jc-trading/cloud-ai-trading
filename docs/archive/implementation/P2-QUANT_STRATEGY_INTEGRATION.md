# Phase 4C - P2: QuantStrategy Integration

**Date:** 2026-04-15  
**Phase:** 4C - User-Configurable Strategies  
**Priority:** P1 (after P1 testing complete)  
**Effort Estimate:** 3-5 days  
**Status:** 📋 Planning Phase

---

## 🎯 Overview

P2 extends the trading signal system to support **user-configurable trading strategies**. Instead of fixed signal weights, users can customize:
- Signal weights for each strategy type
- Risk tolerance and position sizing
- Minimum confidence thresholds
- Backtest historical signal accuracy

**Goal:** Let users optimize the system for their specific trading style.

---

## 📋 Current State (P0 + P1)

### What Works Now
✅ Fixed signal generation pipeline:
- Momentum signal (EMA crossover)
- Contrarian signal (RSI)
- MACD signal (crossover)
- Bollinger Band signal (breakout)

✅ Claude AI analysis:
- Analyzes all 4 signals
- Outputs BUY/SELL/HOLD
- Provides entry/exit prices
- Calculates risk/reward

### What's Missing (P2)
❌ User-configurable strategy parameters
❌ Strategy backtesting capability
❌ Signal accuracy metrics
❌ Strategy comparison tools

---

## 🛠️ P2 Implementation Details

### 1. Data Model: QuantStrategy Table

**New database table: `quant_strategies`**

```sql
CREATE TABLE quant_strategies (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,              -- User who created strategy
    name VARCHAR(100) NOT NULL,            -- e.g., "Aggressive Day Trading"
    description TEXT,                       -- Strategy description
    
    -- Signal weights (must sum to 1.0)
    momentum_weight DECIMAL(3,2),          -- 0.25
    contrarian_weight DECIMAL(3,2),        -- 0.20
    macd_weight DECIMAL(3,2),              -- 0.25
    bollinger_band_weight DECIMAL(3,2),    -- 0.30
    
    -- Risk management
    risk_level VARCHAR(20),                -- "low", "medium", "high"
    min_confidence_threshold INTEGER,      -- 60, 70, 80, 90
    max_position_size DECIMAL(10,2),       -- Units/lots
    
    -- Exit rules
    stop_loss_percent DECIMAL(5,2),        -- 2.5%
    take_profit_percent DECIMAL(5,2),      -- 5.0%
    trailing_stop_percent DECIMAL(5,2),    -- Optional: 3.0%
    
    -- Meta
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    
    -- Backtesting results
    backtest_win_rate DECIMAL(5,2),        -- Accuracy %
    backtest_profit_factor DECIMAL(5,2),   -- Profit/Loss ratio
    backtest_max_drawdown DECIMAL(5,2),    -- Max loss %
    backtest_sharpe_ratio DECIMAL(5,2),    -- Risk-adjusted return
    last_backtest_at TIMESTAMP
);
```

### 2. User Configuration Schema

**API Endpoint to create/update strategy:**

```
POST /api/strategies
PUT  /api/strategies/{strategy_id}
GET  /api/strategies/{strategy_id}
GET  /api/strategies (list all user's strategies)
```

**Request/Response Example:**

```json
{
  "name": "Balanced Growth",
  "description": "Medium risk, balanced signal weighting",
  
  "signal_weights": {
    "momentum": 0.25,
    "contrarian": 0.20,
    "macd": 0.25,
    "bollinger_band": 0.30
  },
  
  "risk_management": {
    "risk_level": "medium",
    "min_confidence": 70,
    "max_position_size": 1000,
    "stop_loss_percent": 2.5,
    "take_profit_percent": 5.0,
    "trailing_stop_percent": 3.0
  },
  
  "is_active": true
}
```

---

## 🔄 Signal Processing with QuantStrategy

### Before P2 (Fixed Weights)
```
4 Signals Generated
    ↓
Claude analyzes strongest signal
    ↓
Output: BUY/SELL/HOLD
```

### After P2 (User-Configurable)
```
4 Signals Generated
    ↓
Apply user strategy weights
    ├─ momentum × 0.25
    ├─ contrarian × 0.20
    ├─ macd × 0.25
    └─ bollinger_band × 0.30
    ↓
Weighted signal score
    ↓
Check min_confidence threshold
    ↓
Claude analyzes (if passes threshold)
    ↓
Output: BUY/SELL/HOLD + position size
```

### Code Implementation

**Location:** `backend/app/modules/trading/strategy.py` (NEW)

```python
class QuantStrategyEngine:
    """Apply user-configurable strategies to signals."""
    
    async def apply_strategy(
        self,
        strategy: QuantStrategy,
        signals: Dict[str, Signal],  # momentum, contrarian, macd, bollinger_band
        current_price: Decimal,
    ) -> StrategyResult:
        """
        Apply strategy weights to generate composite signal.
        
        signals = {
            "momentum": {"type": "BUY", "strength": 75},
            "contrarian": {"type": "HOLD", "strength": 50},
            "macd": {"type": "BUY", "strength": 80},
            "bollinger_band": {"type": "SELL", "strength": 30}
        }
        """
        
        # 1. Weight each signal
        momentum_weighted = signals["momentum"]["strength"] * strategy.momentum_weight
        contrarian_weighted = signals["contrarian"]["strength"] * strategy.contrarian_weight
        macd_weighted = signals["macd"]["strength"] * strategy.macd_weight
        bb_weighted = signals["bollinger_band"]["strength"] * strategy.bollinger_band_weight
        
        # 2. Calculate composite score (0-100)
        composite_score = (
            momentum_weighted + 
            contrarian_weighted + 
            macd_weighted + 
            bb_weighted
        )
        
        # 3. Determine action based on composite score
        if composite_score >= 70:
            action = "BUY"
        elif composite_score <= 30:
            action = "SELL"
        else:
            action = "HOLD"
        
        # 4. Check confidence threshold
        if abs(composite_score - 50) < (100 - strategy.min_confidence_threshold):
            # Confidence too low, don't trade
            action = "HOLD"
        
        # 5. Calculate position size
        position_size = self._calculate_position_size(
            action=action,
            confidence=composite_score,
            max_size=strategy.max_position_size,
            risk_level=strategy.risk_level
        )
        
        return StrategyResult(
            action=action,
            composite_score=composite_score,
            position_size=position_size,
            stop_loss=current_price * (1 - strategy.stop_loss_percent/100),
            take_profit=current_price * (1 + strategy.take_profit_percent/100)
        )
```

---

## 📊 Backtesting Module

### Backtesting Strategy (Against Historical Data)

**New endpoint:**
```
POST /api/strategies/{strategy_id}/backtest
```

**Input:** Strategy ID + Date Range

**Output:** Historical performance metrics

### Implementation

**Location:** `backend/app/modules/analysis/backtester.py` (NEW)

```python
class StrategyBacktester:
    """Backtest strategy against historical signals."""
    
    async def backtest_strategy(
        self,
        strategy: QuantStrategy,
        start_date: datetime,
        end_date: datetime,
        symbol: str
    ) -> BacktestResult:
        """
        Run strategy against historical signal data.
        Calculate win rate, Sharpe ratio, drawdown, etc.
        """
        
        # 1. Get historical signals for date range
        signals = await self.get_historical_signals(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date
        )
        
        # 2. Apply strategy to each signal
        trades = []
        for signal in signals:
            result = await self.apply_strategy(strategy, signal)
            if result.action != "HOLD":
                trades.append(result)
        
        # 3. Calculate performance metrics
        metrics = self._calculate_metrics(trades)
        
        return BacktestResult(
            total_trades=len(trades),
            winning_trades=metrics["wins"],
            losing_trades=metrics["losses"],
            win_rate=metrics["win_rate"],          # %
            profit_factor=metrics["profit_factor"], # Profit/Loss
            max_drawdown=metrics["max_drawdown"],   # %
            sharpe_ratio=metrics["sharpe_ratio"],   # Risk-adjusted return
            cumulative_return=metrics["total_pnl"]  # %
        )
    
    def _calculate_metrics(self, trades: List[Trade]) -> Dict:
        """Calculate performance metrics."""
        
        # Win rate calculation
        winning = [t for t in trades if t.pnl > 0]
        losing = [t for t in trades if t.pnl < 0]
        win_rate = len(winning) / len(trades) if trades else 0
        
        # Profit factor = Total Wins / Total Losses
        total_wins = sum(t.pnl for t in winning)
        total_losses = abs(sum(t.pnl for t in losing))
        profit_factor = total_wins / total_losses if total_losses > 0 else 0
        
        # Sharpe ratio (risk-adjusted return)
        returns = [t.return_pct for t in trades]
        avg_return = sum(returns) / len(returns)
        std_dev = statistics.stdev(returns)
        sharpe_ratio = avg_return / std_dev if std_dev > 0 else 0
        
        # Max drawdown
        cumulative_pnl = 0
        peak_pnl = 0
        max_dd = 0
        for trade in trades:
            cumulative_pnl += trade.pnl
            if cumulative_pnl > peak_pnl:
                peak_pnl = cumulative_pnl
            drawdown = (peak_pnl - cumulative_pnl) / peak_pnl if peak_pnl > 0 else 0
            max_dd = max(max_dd, drawdown)
        
        return {
            "wins": len(winning),
            "losses": len(losing),
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "sharpe_ratio": sharpe_ratio,
            "max_drawdown": max_dd,
            "total_pnl": cumulative_pnl
        }
```

---

## 🎨 Frontend Changes

### New Pages/Components

**1. Strategy Management Page**
```
/strategies
├─ List all user strategies
├─ Create new strategy (form)
├─ Edit existing strategy
└─ Delete strategy
```

**2. Strategy Builder Form**
```
┌─ Strategy Name & Description
├─ Signal Weights (4 sliders)
│  ├─ Momentum: [====--------] 25%
│  ├─ Contrarian: [====------] 20%
│  ├─ MACD: [========------] 25%
│  └─ BB: [==========----] 30%
│
├─ Risk Settings
│  ├─ Risk Level: [Low] [Medium] [High]
│  ├─ Min Confidence: [70%]
│  ├─ Max Position Size: [1000]
│  ├─ Stop Loss: [2.5%]
│  └─ Take Profit: [5.0%]
│
├─ Backtest Button
│  └─ Shows: Win Rate, Profit Factor, Sharpe Ratio, Max DD
│
└─ Save / Cancel
```

**3. Strategy Comparison Tool**
```
Compare up to 3 strategies side-by-side:

Strategy A (Aggressive)  | Strategy B (Balanced)  | Strategy C (Conservative)
─────────────────────────┼────────────────────────┼───────────────────────
Win Rate: 65%            | Win Rate: 58%          | Win Rate: 52%
Profit Factor: 2.1       | Profit Factor: 1.8     | Profit Factor: 1.3
Sharpe Ratio: 1.5        | Sharpe Ratio: 1.2      | Sharpe Ratio: 0.8
Max DD: -15%             | Max DD: -10%           | Max DD: -6%
```

---

## 📈 Integration with Signal Generation

### Modified Trading Task

**Location:** `backend/app/tasks/trading_tasks.py` (MODIFIED)

```python
async def _generate_signal_for_symbol(session, watchlist, symbol):
    """Generate signal for specific symbol."""
    
    # ... existing code for 4 signals ...
    
    # NEW: Load user's active strategy
    user = await session.get(User, watchlist.user_id)
    strategy = await session.get(QuantStrategy, user.active_strategy_id)
    
    # NEW: Apply strategy weights
    if strategy:
        strategy_engine = QuantStrategyEngine()
        strategy_result = await strategy_engine.apply_strategy(
            strategy=strategy,
            signals={
                "momentum": momentum_signal,
                "contrarian": contrarian_signal,
                "macd": macd_signal,
                "bollinger_band": bb_signal
            },
            current_price=latest_candle.close_price
        )
        
        # Use strategy's composite score instead of strongest signal
        composite_action = strategy_result.action
        composite_confidence = strategy_result.composite_score
        position_size = strategy_result.position_size
    else:
        # Fall back to original behavior
        composite_action = strongest_signal.signal_type
        composite_confidence = strongest_signal.confidence
        position_size = 1  # Default position
    
    # NEW: Store strategy applied in signal
    signal.strategy_id = strategy.id if strategy else None
    signal.position_size = position_size
    
    # Claude analysis now receives strategy context
    claude_result = await analyze_with_claude(
        symbol=symbol,
        indicators=indicators_dict,
        strategy=strategy,  # NEW
        suggested_position_size=position_size  # NEW
    )
```

---

## 🔌 API Endpoints (P2)

### Strategy CRUD
```
POST   /api/strategies              # Create new strategy
GET    /api/strategies              # List all user strategies
GET    /api/strategies/{id}         # Get single strategy
PUT    /api/strategies/{id}         # Update strategy
DELETE /api/strategies/{id}         # Delete strategy
```

### Backtesting
```
POST   /api/strategies/{id}/backtest    # Run backtest
GET    /api/backtest-results/{id}       # Get backtest results
```

### Analytics
```
GET    /api/strategies/compare          # Compare strategies
GET    /api/strategies/{id}/performance # Performance over time
```

---

## 📊 Example: Pre-built Strategies

Users can choose from preset strategies:

### Strategy 1: Conservative
```json
{
  "name": "Conservative Growth",
  "momentum_weight": 0.15,
  "contrarian_weight": 0.35,    // Higher contrarian (safer)
  "macd_weight": 0.25,
  "bollinger_band_weight": 0.25,
  "risk_level": "low",
  "min_confidence": 80,          // High threshold
  "stop_loss_percent": 1.5,      // Tight stop loss
  "take_profit_percent": 3.0
}
```

### Strategy 2: Balanced
```json
{
  "name": "Balanced Growth",
  "momentum_weight": 0.25,
  "contrarian_weight": 0.20,
  "macd_weight": 0.25,
  "bollinger_band_weight": 0.30,
  "risk_level": "medium",
  "min_confidence": 70,
  "stop_loss_percent": 2.5,
  "take_profit_percent": 5.0
}
```

### Strategy 3: Aggressive
```json
{
  "name": "Aggressive Trading",
  "momentum_weight": 0.35,       // Higher momentum (faster trades)
  "contrarian_weight": 0.10,
  "macd_weight": 0.30,
  "bollinger_band_weight": 0.25,
  "risk_level": "high",
  "min_confidence": 50,          // Lower threshold
  "stop_loss_percent": 3.5,      // Wider stop loss
  "take_profit_percent": 7.0
}
```

---

## 🧪 Testing Strategy (P2)

### Unit Tests
```
tests/test_p2_strategy.py
├─ TestQuantStrategyCreation
├─ TestStrategyWeightApplication
├─ TestPositionSizeCalculation
├─ TestBacktestEngine
└─ TestStrategyComparison
```

### Integration Tests
```
1. Create strategy via API
2. Generate signals with strategy applied
3. Verify weighted signals in database
4. Run backtest
5. Compare to baseline (fixed weights)
```

---

## 📅 Development Timeline

### Phase 1: Backend (2 days)
- [x] Design QuantStrategy schema
- [ ] Create database table + migrations
- [ ] Implement strategy CRUD endpoints
- [ ] Implement QuantStrategyEngine
- [ ] Integrate with signal generation
- [ ] Write unit tests

### Phase 2: Backtesting (1 day)
- [ ] Implement StrategyBacktester
- [ ] Calculate performance metrics
- [ ] Store backtest results
- [ ] Create backtesting API endpoints

### Phase 3: Frontend (1 day)
- [ ] Strategy management page
- [ ] Strategy builder form
- [ ] Backtest UI
- [ ] Strategy comparison tool

### Phase 4: Integration & Testing (1 day)
- [ ] End-to-end testing
- [ ] Performance validation
- [ ] Documentation
- [ ] Demo preset strategies

**Total Effort: 5 days**

---

## 🎯 Success Criteria

✅ **Core Features**
- Users can create/edit custom strategies
- Strategies are applied to signal generation
- Backtesting works against historical data
- Multiple strategies can be compared

✅ **Performance**
- Strategy calculation < 100ms
- Backtest on 30 days of data < 5 seconds
- No impact on signal generation speed

✅ **Usability**
- Preset strategies provided
- UI is intuitive and clear
- Documentation is complete

---

## 🚀 Post-P2 (P3+)

### P3: Auto Position Management
- Automatic position sizing based on Kelly Criterion
- Stop-loss and take-profit execution
- Position tracking and P&L calculation

### P4: Telegram Notifications
- Send trading signals via Telegram
- Include strategy and position size info

### P5: Live Trading (Binance Integration)
- Execute trades on Binance based on signals
- Order management and settlement

---

## 📚 Related Documentation

- `CLAUDE.md` - Project navigation
- `SIGNAL_GENERATION_FREQUENCY.md` - Current system frequency
- `P0-PHASE_4_CLAUDE_AI_INTEGRATION.md` - P0 details
- `P1-PHASE_4_EXTENDED_SIGNALS.md` - P1 details
- `PROJECT_STATUS.md` - Overall status

---

**Document Created:** 2026-04-15  
**Status:** 📋 Planning Phase  
**Next Action:** Start Backend Implementation  
**Estimated Completion:** 2026-04-20

