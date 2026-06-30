# P3 Phase 3A - Quick Reference Guide

## Position Sizing Examples

### Example 1: Conservative Low-Risk Trade

**Input Parameters:**
- Account equity: $50,000
- Entry price: $100 (BTC/USDT)
- Signal strength: 60 (moderate signal)
- Risk level: low

**Calculation:**
```
Max risk per trade: 2% × $50,000 = $1,000
SL distance: 2.0% (low risk setting)
Base position: $1,000 / 0.02 = $50,000
Signal multiplier: 60% = 0.6
Risk multiplier: low = 0.5x
Final position: $50,000 × 0.6 × 0.5 = $15,000
Max allowed (5%): $50,000 × 0.05 = $2,500
Final size: min($15,000, $2,500) = $2,500

SL Price: $100 × (1 - 0.02) = $98.00
TP Price: $100 × (1 + 0.05) = $105.00
Max loss: $2,500 × 0.02 = $50
Risk/Reward: 5% / 2% = 2.5:1
```

### Example 2: Balanced Medium-Risk Trade

**Input Parameters:**
- Account equity: $50,000
- Entry price: $2,500 (ETH/USDT)
- Signal strength: 80 (strong signal)
- Risk level: medium

**Calculation:**
```
Max risk per trade: 2% × $50,000 = $1,000
SL distance: 2.5% (medium risk setting)
Base position: $1,000 / 0.025 = $40,000
Signal multiplier: 80% = 0.8
Risk multiplier: medium = 1.0x
Initial position: $40,000 × 0.8 × 1.0 = $32,000
Max allowed (5%): $50,000 × 0.05 = $2,500
Final size: min($32,000, $2,500) = $2,500

SL Price: $2,500 × (1 - 0.025) = $2,437.50
TP Price: $2,500 × (1 + 0.075) = $2,687.50
Max loss: $2,500 × 0.025 = $62.50
Risk/Reward: 7.5% / 2.5% = 3.0:1
```

### Example 3: Aggressive High-Risk Trade

**Input Parameters:**
- Account equity: $50,000
- Entry price: $1 (SHIB/USDT)
- Signal strength: 95 (very strong signal)
- Risk level: high

**Calculation:**
```
Max risk per trade: 2% × $50,000 = $1,000
SL distance: 3.0% (high risk setting)
Base position: $1,000 / 0.03 = $33,333
Signal multiplier: 95% = 0.95
Risk multiplier: high = 1.5x
Initial position: $33,333 × 0.95 × 1.5 = $47,500
Max allowed (5%): $50,000 × 0.05 = $2,500
Final size: min($47,500, $2,500) = $2,500

SL Price: $1.00 × (1 - 0.03) = $0.97
TP Price: $1.00 × (1 + 0.10) = $1.10
Max loss: $2,500 × 0.03 = $75
Risk/Reward: 10% / 3% = 3.33:1
```

---

## Validation Examples

### Position Size Validation

```python
# Valid: Within all limits
validate_position_size(
    position_size=Decimal("2500"),      # Within 5% limit
    account_equity=Decimal("50000"),
    max_position_percent=Decimal("5"),
    min_size=Decimal("10")
)
# Result: (True, None)

# Invalid: Exceeds position limit
validate_position_size(
    position_size=Decimal("10000"),     # 20% of account
    account_equity=Decimal("50000"),
    max_position_percent=Decimal("5"),
    min_size=Decimal("10")
)
# Result: (False, "Position size $10000 exceeds max $2500 (5% of equity)")

# Invalid: Below minimum size
validate_position_size(
    position_size=Decimal("5"),         # Below $10 minimum
    account_equity=Decimal("50000"),
    max_position_percent=Decimal("5"),
    min_size=Decimal("10")
)
# Result: (False, "Position size $5 below minimum $10")
```

### Stop Loss Validation

```python
# Valid: LONG position with proper SL
validate_stop_loss(
    entry_price=Decimal("100"),
    stop_loss_price=Decimal("97.5"),    # 2.5% below entry
    position_type="LONG",
    min_distance_percent=Decimal("0.5")
)
# Result: (True, None)

# Invalid: SL too close for LONG
validate_stop_loss(
    entry_price=Decimal("100"),
    stop_loss_price=Decimal("99.7"),    # Only 0.3% below
    position_type="LONG",
    min_distance_percent=Decimal("0.5")
)
# Result: (False, "SL distance 0.30% below minimum 0.50%")

# Invalid: SL above entry for LONG
validate_stop_loss(
    entry_price=Decimal("100"),
    stop_loss_price=Decimal("105"),     # Above entry (wrong direction)
    position_type="LONG"
)
# Result: (False, "For LONG: SL $105 must be below entry $100")
```

### Take Profit Validation

```python
# Valid: TP above entry for LONG
validate_take_profit(
    entry_price=Decimal("100"),
    take_profit_price=Decimal("107.5"), # 7.5% above entry
    position_type="LONG",
    min_distance_percent=Decimal("1.0")
)
# Result: (True, None)

# Valid: TP below entry for SHORT
validate_take_profit(
    entry_price=Decimal("100"),
    take_profit_price=Decimal("92.5"),  # 7.5% below entry
    position_type="SHORT",
    min_distance_percent=Decimal("1.0")
)
# Result: (True, None)

# Invalid: TP too close
validate_take_profit(
    entry_price=Decimal("100"),
    take_profit_price=Decimal("100.5"), # Only 0.5% above
    position_type="LONG",
    min_distance_percent=Decimal("1.0")
)
# Result: (False, "TP distance 0.50% below minimum 1.00%")
```

### Risk Limit Validation

```python
# Valid: Consistent risk limits
validate_risk_limits(
    max_position_percent=Decimal("5.0"),        # 5% per position
    max_loss_percent=Decimal("2.0"),            # 2% per trade
    max_portfolio_loss_percent=Decimal("10.0"), # 10% portfolio
    daily_loss_percent=Decimal("3.0"),          # 3% daily
    max_open_positions=10,
    max_concentration_percent=Decimal("30.0")   # 30% in single asset
)
# Result: (True, None)

# Invalid: Negative values
validate_risk_limits(
    max_position_percent=Decimal("-5.0"),
    max_loss_percent=Decimal("2.0"),
    max_portfolio_loss_percent=Decimal("10.0"),
    daily_loss_percent=Decimal("3.0"),
    max_open_positions=10,
    max_concentration_percent=Decimal("30.0")
)
# Result: (False, "max_position_percent must be positive")

# Invalid: Concentration > 100%
validate_risk_limits(
    max_position_percent=Decimal("5.0"),
    max_loss_percent=Decimal("2.0"),
    max_portfolio_loss_percent=Decimal("10.0"),
    daily_loss_percent=Decimal("3.0"),
    max_open_positions=10,
    max_concentration_percent=Decimal("150.0")  # Invalid!
)
# Result: (False, "max_concentration_percent cannot exceed 100%")
```

---

## Risk Level Configuration

### Low Risk Profile
```python
RISK_LEVEL_CONFIG = {
    "low": {
        "size_multiplier": 0.5,      # 50% of base position
        "sl_distance": 2.0,          # 2% stop loss
        "tp_distance": 5.0,          # 5% take profit
        # Risk-Reward Ratio: 5% / 2% = 2.5:1
    }
}
```
**Use for:** Conservative traders, large accounts, volatile assets

### Medium Risk Profile
```python
RISK_LEVEL_CONFIG = {
    "medium": {
        "size_multiplier": 1.0,      # 100% of base position
        "sl_distance": 2.5,          # 2.5% stop loss
        "tp_distance": 7.5,          # 7.5% take profit
        # Risk-Reward Ratio: 7.5% / 2.5% = 3.0:1
    }
}
```
**Use for:** Balanced traders, medium accounts, standard volatility

### High Risk Profile
```python
RISK_LEVEL_CONFIG = {
    "high": {
        "size_multiplier": 1.5,      # 150% of base position
        "sl_distance": 3.0,          # 3% stop loss
        "tp_distance": 10.0,         # 10% take profit
        # Risk-Reward Ratio: 10% / 3% = 3.33:1
    }
}
```
**Use for:** Aggressive traders, small accounts, high conviction trades

---

## Signal Strength Interpretation

| Strength | Interpretation | Position Multiplier |
|----------|-----------------|-------------------|
| 50 | Minimal signal | 0.5x (50%) |
| 60 | Weak signal | 0.6x (60%) |
| 70 | Moderate signal | 0.7x (70%) |
| 75 | Good signal | 0.75x (75%) |
| 80 | Strong signal | 0.8x (80%) |
| 85 | Very strong signal | 0.85x (85%) |
| 90 | Excellent signal | 0.9x (90%) |
| 95 | Outstanding signal | 0.95x (95%) |
| 100 | Perfect signal | 1.0x (100%) |

**Example:** With medium risk level and 80 strength:
- Base position: $30,000
- Multiplier: 0.8 × 1.0 = 0.8
- Actual position: $30,000 × 0.8 = $24,000

---

## Portfolio Limits Reference

### Default Limits (All Configurable)

| Limit | Value | Rationale |
|-------|-------|-----------|
| Max position size | 5% | Prevent overconcentration |
| Max loss per position | 2% | Limit single trade damage |
| Max portfolio loss | 10% | Stop-loss for entire portfolio |
| Daily loss limit | 3% | Prevent consecutive bad days |
| Max open positions | 10 | Diversification benefit |
| Max concentration | 30% | Correlation risk limit |
| Min signal strength | 50 | Avoid weak signals |
| Max position age | 7 days | Don't hold losing positions |
| Max consecutive losses | 3 | Pause after downtrend |

### Recommended Configurations

**Conservative (for $100k+ accounts):**
```
Max position: 2%
Max loss/trade: 1%
Max portfolio loss: 5%
Daily loss: 2%
Max open: 5
Max concentration: 20%
Risk level: low
```

**Balanced (for $10k-$100k accounts):**
```
Max position: 5%
Max loss/trade: 2%
Max portfolio loss: 10%
Daily loss: 3%
Max open: 10
Max concentration: 30%
Risk level: medium
```

**Aggressive (for $1k-$10k accounts):**
```
Max position: 10%
Max loss/trade: 3%
Max portfolio loss: 15%
Daily loss: 5%
Max open: 15
Max concentration: 50%
Risk level: high
```

---

## API Usage Examples

### Calculate Position Size

```bash
curl -X POST http://localhost:8000/api/risk/positions/calculate-size \
  -H "Authorization: Bearer TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "BTCUSDT",
    "entry_price": 45000,
    "signal_strength": 75,
    "account_equity": 50000
  }'

Response:
{
  "symbol": "BTCUSDT",
  "position_size": 2500,
  "stop_loss_price": 43875,
  "take_profit_price": 48375,
  "max_loss": 62.5,
  "risk_reward_ratio": 3.0,
  "reason": "Position size calculated successfully"
}
```

### Validate Position

```bash
curl -X POST http://localhost:8000/api/risk/positions/validate \
  -H "Authorization: Bearer TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "ETHUSDT",
    "position_size": 2500
  }'

Response:
{
  "is_allowed": true,
  "reason": "Position allowed"
}
```

### Get Risk Limits

```bash
curl -X GET http://localhost:8000/api/risk/limits/watchlist-id \
  -H "Authorization: Bearer TOKEN"

Response:
{
  "watchlist_id": "550e8400-e29b-41d4-a716-446655440000",
  "max_position_size_percent": 5.0,
  "max_loss_per_trade_percent": 2.0,
  "max_portfolio_loss_percent": 10.0,
  "daily_loss_limit_percent": 3.0,
  "max_open_positions": 10,
  "max_concentration_percent": 30.0,
  "risk_level": "medium",
  "position_sizing_method": "risk_weighted"
}
```

### Update Risk Limits

```bash
curl -X PATCH http://localhost:8000/api/risk/limits/watchlist-id \
  -H "Authorization: Bearer TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "max_position_size_percent": 4.0,
    "max_loss_per_trade_percent": 1.5,
    "risk_level": "low"
  }'
```

---

## Common Validation Errors & Fixes

| Error | Cause | Fix |
|-------|-------|-----|
| "Position exceeds max" | Size > 5% of account | Reduce position size or increase account |
| "SL distance too small" | < 0.5% from entry | Widen stop loss distance |
| "TP distance too small" | < 1.0% from entry | Set more realistic profit target |
| "Daily loss limit reached" | Lost > 3% today | Stop trading until next day |
| "Concentration too high" | Single asset > 30% | Diversify or reduce position |
| "Max open positions" | Already have 10 open | Close losing trades first |
| "Signal strength low" | < 50 (minimum) | Wait for stronger signal |

---

## Performance Tuning

### For Large Portfolios (100+ positions)

1. **Enable caching:**
   ```python
   risk_limits = await cache.get_or_fetch(
       f"risk_limits_{watchlist_id}",
       lambda: RiskEngine._get_risk_limits(db, watchlist_id),
       ttl=3600  # Cache for 1 hour
   )
   ```

2. **Batch operations:**
   ```python
   # Check all positions at once
   await RiskEngine.check_portfolio_limits(db, watchlist_id, equity, pnl)
   ```

3. **Use database indexes:**
   - `watchlist_id` on all tables
   - `recorded_at` on metrics tables
   - Composite index on (watchlist_id, symbol)

---

**Quick Reference Saved: 2026-04-15**
