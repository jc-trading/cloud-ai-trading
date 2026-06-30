# P2 Development Quick Start

**Date:** 2026-04-15  
**Status:** Ready to Begin Implementation  
**Effort:** 5 days  
**Team:** Backend (2d) + Backtesting (1d) + Frontend (1d) + Testing (1d)

---

## ✅ P0 + P1 Status

**P0 (Claude AI Integration)**
- ✅ Code implementation complete
- ✅ Code review complete
- ✅ Tested on Claude Code CLI

**P1 (Extended Signals - MACD + Bollinger Band)**
- ✅ Code implementation complete
- ✅ 12/12 unit tests passing
- ✅ Integration test framework ready
- ✅ Tested on Claude Code CLI

**Current Signal Flow:**
```
Market Data (1min) → 4 Signal Types → Claude AI Analysis → Result
                                    └─ All 4 signals analyzed together
```

---

## 📋 P2 Overview

**Goal:** Enable users to configure their own trading strategies

**Current:** Fixed signal weights and rules  
**After P2:** User-customizable strategies with backtesting

### Key Features

1. **Strategy Management**
   - Create/edit/delete custom strategies
   - Preset strategies (Conservative, Balanced, Aggressive)
   - Active strategy selection

2. **Configurable Parameters**
   - Signal weights (Momentum, Contrarian, MACD, BB)
   - Risk levels (low, medium, high)
   - Position sizing rules
   - Stop loss / take profit percentages

3. **Backtesting**
   - Test strategy against historical data
   - Calculate performance metrics:
     - Win rate (%)
     - Profit factor (Profit/Loss)
     - Sharpe ratio (risk-adjusted return)
     - Maximum drawdown (%)

4. **Strategy Comparison**
   - Compare up to 3 strategies side-by-side
   - Performance metrics comparison

---

## 🗂️ File Structure for P2

```
backend/
├── app/
│   ├── models/
│   │   └── quant_strategy.py          [NEW]
│   │       └── QuantStrategy model
│   │
│   ├── modules/
│   │   ├── strategy/                   [NEW FOLDER]
│   │   │   ├── __init__.py
│   │   │   ├── engine.py               [NEW] QuantStrategyEngine
│   │   │   ├── models.py               [NEW] Data models
│   │   │   └── schemas.py              [NEW] Pydantic schemas
│   │   │
│   │   ├── analysis/
│   │   │   ├── backtester.py           [NEW] StrategyBacktester
│   │   │   └── claude.py               [MODIFY] Add strategy context
│   │   │
│   │   └── trading/
│   │       └── signals.py              [MODIFY] Accept strategy param
│   │
│   ├── api/
│   │   └── routes/
│   │       └── strategies.py           [NEW] CRUD endpoints
│   │
│   └── tasks/
│       └── trading_tasks.py            [MODIFY] Apply strategy weights
│
frontend/
├── src/
│   ├── views/
│   │   └── StrategyBuilder.vue         [NEW] UI for creating strategies
│   │
│   ├── components/
│   │   ├── StrategyForm.vue            [NEW] Form component
│   │   ├── BacktestResults.vue         [NEW] Display backtest metrics
│   │   └── StrategyComparison.vue      [NEW] Compare strategies
│   │
│   └── stores/
│       └── strategyStore.js            [NEW] Pinia store
│
tests/
├── test_p2_strategy.py                 [NEW] Backend tests
└── test_p2_frontend.py                 [NEW] Frontend tests
```

---

## 🚀 Development Order

### Phase 1: Backend - Database & Models (Day 1)

**Step 1: Create database migration**
```bash
# File: backend/alembic/versions/xxxx_add_quant_strategy.py

# Create quant_strategies table with:
# - User ID (FK to users table)
# - Signal weights (momentum, contrarian, macd, bollinger_band)
# - Risk parameters (risk_level, min_confidence, max_position_size)
# - Exit rules (stop_loss_percent, take_profit_percent, trailing_stop)
# - Backtest results (win_rate, profit_factor, sharpe_ratio, max_drawdown)
```

**Step 2: Create models**
```bash
# File: backend/app/modules/strategy/models.py

class QuantStrategy(Base):
    __tablename__ = "quant_strategies"
    
    # Core fields (implement in code)
```

**Step 3: Create API schemas**
```bash
# File: backend/app/modules/strategy/schemas.py

class StrategyCreate(BaseModel):
class StrategyUpdate(BaseModel):
class StrategyResponse(BaseModel):
class BacktestResult(BaseModel):
```

### Phase 2: Backend - Strategy Engine (Day 1-2)

**Step 4: Implement QuantStrategyEngine**
```bash
# File: backend/app/modules/strategy/engine.py

class QuantStrategyEngine:
    async def apply_strategy() → StrategyResult
    async def calculate_position_size() → Decimal
    async def validate_weights() → bool
```

**Step 5: Create CRUD endpoints**
```bash
# File: backend/app/api/routes/strategies.py

POST   /api/strategies              # Create
GET    /api/strategies              # List
GET    /api/strategies/{id}         # Get
PUT    /api/strategies/{id}         # Update
DELETE /api/strategies/{id}         # Delete
```

**Step 6: Modify signal generation task**
```bash
# File: backend/app/tasks/trading_tasks.py

# Load user's active strategy
# Apply strategy weights to signals
# Calculate position size based on strategy
# Pass strategy context to Claude
```

### Phase 3: Backend - Backtesting (Day 2-3)

**Step 7: Implement StrategyBacktester**
```bash
# File: backend/app/modules/analysis/backtester.py

class StrategyBacktester:
    async def backtest_strategy() → BacktestResult
    def _calculate_metrics() → Dict[str, float]
    # Metrics: win_rate, profit_factor, sharpe_ratio, max_drawdown
```

**Step 8: Create backtest endpoints**
```bash
# File: backend/app/api/routes/strategies.py

POST   /api/strategies/{id}/backtest     # Run backtest
GET    /api/backtest-results/{id}        # Get results
```

### Phase 4: Frontend - UI (Day 3-4)

**Step 9: Create strategy pages**
```bash
# File: frontend/src/views/StrategyBuilder.vue

- List all strategies
- Create new strategy button
- Edit existing strategy
- Delete strategy with confirmation
```

**Step 10: Create strategy form component**
```bash
# File: frontend/src/components/StrategyForm.vue

- Text input for name/description
- 4 sliders for signal weights (must sum to 100%)
- Risk level selector
- Confidence threshold slider
- Position size input
- Stop loss / Take profit percentages
- Backtest button
- Save / Cancel buttons
```

**Step 11: Create backtest results display**
```bash
# File: frontend/src/components/BacktestResults.vue

- Win rate percentage
- Profit factor number
- Sharpe ratio number
- Max drawdown percentage
- Cumulative return chart
```

**Step 12: Create strategy comparison tool**
```bash
# File: frontend/src/components/StrategyComparison.vue

- Side-by-side comparison of 3 strategies
- Metrics table
- Visual comparison charts
```

### Phase 5: Testing & Integration (Day 4-5)

**Step 13: Write unit tests**
```bash
# File: tests/test_p2_strategy.py

- Test strategy creation
- Test weight validation
- Test position size calculation
- Test backtest calculations
- Test metric calculations
```

**Step 14: Integration testing**
```bash
- Create strategy via API
- Generate signals with strategy applied
- Run backtest
- Verify results in database
- Test strategy comparison
```

**Step 15: Documentation & Demo**
```bash
- Update API documentation
- Create preset strategies
- Write user guide
- Create demo video
```

---

## 📄 Key Code Templates

### 1. Database Migration Template

```python
# backend/alembic/versions/xxxx_add_quant_strategy.py

def upgrade():
    op.create_table(
        'quant_strategies',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('momentum_weight', sa.Numeric(3, 2), nullable=False),
        sa.Column('contrarian_weight', sa.Numeric(3, 2), nullable=False),
        sa.Column('macd_weight', sa.Numeric(3, 2), nullable=False),
        sa.Column('bollinger_band_weight', sa.Numeric(3, 2), nullable=False),
        sa.Column('risk_level', sa.String(20), nullable=False),
        sa.Column('min_confidence_threshold', sa.Integer(), nullable=False),
        # ... more columns
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id')
    )
```

### 2. Strategy Engine Template

```python
# backend/app/modules/strategy/engine.py

class QuantStrategyEngine:
    async def apply_strategy(
        self,
        strategy: QuantStrategy,
        signals: Dict[str, Signal]
    ) -> StrategyResult:
        """Apply strategy weights to signals."""
        
        # 1. Weight each signal
        # 2. Calculate composite score
        # 3. Determine action
        # 4. Calculate position size
        # 5. Return result
```

### 3. Backtester Template

```python
# backend/app/modules/analysis/backtester.py

class StrategyBacktester:
    async def backtest_strategy(
        self,
        strategy: QuantStrategy,
        start_date: datetime,
        end_date: datetime
    ) -> BacktestResult:
        """Backtest strategy against historical signals."""
        
        # 1. Get historical signals
        # 2. Apply strategy to each signal
        # 3. Calculate metrics
        # 4. Return BacktestResult
```

---

## ✅ Testing Checklist

### Backend Tests
- [ ] Strategy CRUD operations
- [ ] Weight validation (sum to 100%)
- [ ] Position size calculation
- [ ] Backtest accuracy
- [ ] Metric calculations
- [ ] Error handling

### Frontend Tests
- [ ] Form validation
- [ ] Weight sliders
- [ ] Strategy creation flow
- [ ] Backtest UI
- [ ] Comparison tool

### Integration Tests
- [ ] End-to-end strategy creation
- [ ] Signal generation with strategy
- [ ] Backtest execution
- [ ] Data consistency

---

## 📊 Expected Outcomes

### By End of P2

✅ Users can:
- Create custom trading strategies
- Adjust signal weights and risk parameters
- Backtest strategies against historical data
- Compare multiple strategies
- Set active strategy for live trading

✅ System improvement:
- Personalized trading signals
- Better risk management
- Data-driven strategy selection

✅ Cost impact:
- No additional API costs
- Slightly more database queries (minimal impact)

---

## 🔗 Dependencies

### External APIs
- None (all processing local)

### Database
- ✅ PostgreSQL (already running)

### New Python Packages
- None (using existing packages)

### New Frontend Packages
- None (using existing Vue 3 + Pinia)

---

## 📞 Questions Before Starting?

**Architecture concerns?** → Review `P2-QUANT_STRATEGY_INTEGRATION.md`  
**Database schema?** → Check the schema definition in P2 doc  
**API design?** → See the endpoint list in P2 doc  
**Frontend components?** → Reference the component structure above  

---

## 🎯 Definition of Success

✅ Users can create and manage custom strategies  
✅ Strategies apply weights to signals correctly  
✅ Backtesting works and produces accurate metrics  
✅ System performance not degraded  
✅ All tests passing  
✅ Documentation complete  

---

**Ready to start P2 development!** 🚀

**Next Step:** Begin with Phase 1 (Database & Models)  
**Estimated Completion:** 2026-04-20

