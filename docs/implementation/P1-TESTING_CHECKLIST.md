# P1 Testing Checklist: Extended Signal Generation

**Phase:** Phase 4 - P1: MACD + Bollinger Band Signals  
**Date:** 2026-04-14  
**Status:** 🟡 READY FOR TESTING  

---

## 🧪 Testing Overview

P1 adds 2 new signals (MACD, Bollinger Band) to complement existing 2 signals (Momentum, Contrarian). All 4 signals analyzed by Claude AI.

**Key Changes:**
- 4 signals per symbol per minute (instead of 2)
- Claude AI analyzes signal convergence/divergence
- Database stores all 4 signal strategies

---

## ✅ Unit Tests

### MACD Signal Generation

#### Test 1.1: Bullish MACD Crossover
```python
# Input: MACD crosses above signal line
macd_current = 150, macd_signal = 100
macd_prev = 50, macd_signal_prev = 100

# Expected:
signal_type = "STRONG_BUY"
signal_strength = 100
confidence = 90
```

**Checklist:**
- [ ] Correctly identifies crossover
- [ ] Sets strength to 100
- [ ] Sets confidence to 90
- [ ] Recommendation mentions crossover

#### Test 1.2: Bearish MACD Crossover
```python
# Input: MACD crosses below signal line
macd_current = 50, macd_signal = 100
macd_prev = 150, macd_signal_prev = 100

# Expected:
signal_type = "STRONG_SELL"
signal_strength = 0
confidence = 90
```

**Checklist:**
- [ ] Correctly identifies bearish crossover
- [ ] Sets strength to 0
- [ ] Sets confidence to 90

#### Test 1.3: MACD Bullish Trend (No Crossover)
```python
# Input: MACD > Signal, no recent crossover
macd_current = 150, macd_signal = 100
macd_prev = 140, macd_signal_prev = 95

# Expected:
signal_type = "BUY"
signal_strength = 60-75 (based on distance)
confidence = 70-80
```

**Checklist:**
- [ ] BUY signal when MACD > Signal
- [ ] Strength increases with distance
- [ ] Confidence reasonable

---

### Bollinger Band Signal Generation

#### Test 2.1: Upper Breakout
```python
# Input: Price breaks above BB upper
current_price = 105, bb_upper = 100, bb_middle = 50, bb_lower = 5

# Expected:
signal_type = "STRONG_BUY"
signal_strength = 100
confidence = 85
```

**Checklist:**
- [ ] STRONG_BUY when price > BB upper
- [ ] Strength = 100
- [ ] Confidence = 85

#### Test 2.2: Lower Breakout
```python
# Input: Price breaks below BB lower
current_price = 3, bb_upper = 100, bb_middle = 50, bb_lower = 5

# Expected:
signal_type = "STRONG_SELL"
signal_strength = 0
confidence = 85
```

**Checklist:**
- [ ] STRONG_SELL when price < BB lower
- [ ] Strength = 0
- [ ] Confidence = 85

#### Test 2.3: Price Near Upper Band
```python
# Input: Price close to upper band but not breaking
current_price = 98, bb_upper = 100, bb_middle = 50, bb_lower = 5
band_width = 95

# Expected:
signal_type = "BUY"
signal_strength = 65-70
confidence = 75
```

**Checklist:**
- [ ] BUY when approaching upper band
- [ ] Strength between 65-75
- [ ] Confidence = 75

---

## ✅ Integration Tests

### Test 3.1: All 4 Signals Generated Per Symbol

```bash
# Run Celery task for 1 watchlist with 1 symbol
# Expected: 4 signals saved to database per minute
```

**SQL Verification:**
```sql
SELECT strategy, COUNT(*) as signal_count
FROM trading_signals
WHERE symbol = 'BTCUSDT'
  AND created_at > NOW() - INTERVAL '5 minutes'
GROUP BY strategy;

-- Expected output:
-- MOMENTUM | 5
-- CONTRARIAN | 5
-- MACD | 5
-- BOLLINGER_BAND | 5
```

**Checklist:**
- [ ] 4 signals per symbol per minute
- [ ] All 4 strategies present
- [ ] No duplicates

### Test 3.2: Claude AI Receives All 4 Signals

```sql
-- Check if Claude analysis includes all 4 signal types
SELECT 
  indicators_used->'claude_analysis'->'all_signals'->>'momentum' as momentum_signal,
  indicators_used->'claude_analysis'->'all_signals'->>'contrarian' as contrarian_signal,
  indicators_used->'claude_analysis'->'all_signals'->>'macd' as macd_signal,
  indicators_used->'claude_analysis'->'all_signals'->>'bollinger_band' as bb_signal
FROM trading_signals
WHERE indicators_used->'claude_analysis' IS NOT NULL
  AND created_at > NOW() - INTERVAL '10 minutes'
LIMIT 5;
```

**Checklist:**
- [ ] All 4 signals present in Claude analysis
- [ ] Signal types correct
- [ ] Strength values within 0-100

### Test 3.3: Signal Convergence Analysis

```
Example convergence scenarios:
- All 4 = BUY → Claude STRONG_BUY (high confidence)
- 3 = BUY, 1 = HOLD → Claude BUY (medium confidence)
- 2 = BUY, 2 = SELL → Claude HOLD (low confidence)
- All 4 = SELL → Claude STRONG_SELL (high confidence)
```

**Checklist:**
- [ ] Claude confidence reflects signal convergence
- [ ] Higher convergence = higher confidence
- [ ] Reason mentions signal agreement/disagreement

---

## ✅ Performance Tests

### Test 4.1: Task Execution Time

```bash
# Monitor Celery task duration
docker compose logs celery-worker | grep "generate_trading_signals"

# Expected: < 10 seconds for all symbols
```

**Baseline (P0):** 5-7 seconds  
**Expected (P1):** 6-8 seconds (additional MACD + BB calculation)

**Checklist:**
- [ ] Task completes < 10 seconds
- [ ] No timeout errors
- [ ] Performance degradation < 20%

### Test 4.2: Database Query Performance

```sql
-- Check MACD query performance
EXPLAIN ANALYZE
SELECT * FROM trading_signals
WHERE strategy = 'MACD'
  AND created_at > NOW() - INTERVAL '1 hour'
  AND signal_type = 'STRONG_BUY';

-- Expected: < 100ms execution time
```

**Checklist:**
- [ ] Queries execute < 100ms
- [ ] Indexes being used
- [ ] No sequential scans

### Test 4.3: Memory Usage

```bash
# Monitor memory during task execution
docker stats cloudaitrading_celery-worker --no-stream

# Expected: < 500MB increase from baseline
```

**Checklist:**
- [ ] Memory usage reasonable
- [ ] No memory leaks over time

---

## ✅ Data Validation Tests

### Test 5.1: MACD Signal Strength Calculation

For 5 randomly selected MACD signals:

```sql
SELECT 
  symbol,
  signal_strength,
  indicators_used->'MACD' as macd_value,
  indicators_used->'MACD_Signal' as macd_signal_value
FROM trading_signals
WHERE strategy = 'MACD'
  AND created_at > NOW() - INTERVAL '10 minutes'
LIMIT 5;
```

Verify:
- [ ] STRONG_BUY has strength 100 or high BUY (60-75)
- [ ] STRONG_SELL has strength 0 or low SELL (25-40)
- [ ] HOLD has strength ~50
- [ ] All values 0-100

### Test 5.2: Bollinger Band Signal Consistency

```sql
SELECT 
  symbol,
  signal_strength,
  signal_type,
  indicators_used->>'Price' as price,
  indicators_used->>'BB_Upper' as bb_upper,
  indicators_used->>'BB_Lower' as bb_lower
FROM trading_signals
WHERE strategy = 'BOLLINGER_BAND'
  AND created_at > NOW() - INTERVAL '10 minutes'
LIMIT 5;
```

Verify:
- [ ] If price > BB_upper → STRONG_BUY or high strength
- [ ] If price < BB_lower → STRONG_SELL or low strength
- [ ] If price in middle → HOLD or ~50 strength
- [ ] Signal_strength increases with distance from band

---

## ✅ Telegram Notification Tests

### Test 6.1: STRONG Signal Notifications

Generate 1 MACD STRONG_BUY or STRONG_SELL:

```bash
# Wait for signal
# Check Telegram notification
# Expected message includes:
# - Symbol
# - All signal types (Momentum, Contrarian, MACD, BB)
# - Updated Claude confidence
# - Entry/exit prices
```

**Checklist:**
- [ ] Notification sends
- [ ] Includes symbol
- [ ] Includes Claude recommendation
- [ ] Includes entry/exit prices
- [ ] Message readable

---

## ✅ Celery Task Tests

### Test 7.1: Task Completes Without Error

```bash
# Watch Celery worker logs
docker compose logs -f celery-worker

# Run 1 cycle of signal generation
# Expected:
# - No ERROR or CRITICAL logs
# - All signals saved
# - Claude analysis completed (if API key set)
```

**Checklist:**
- [ ] Task completes
- [ ] No errors in logs
- [ ] All signals in database
- [ ] Claude analysis attempted (if API available)

### Test 7.2: Error Handling

#### Test 7.2.1: No Candle Data
```
Disable candle fetching
Run task
Expected: Logs warning "No candle data for {symbol}", skips symbol
```

**Checklist:**
- [ ] Graceful skip on missing data
- [ ] Error logged as warning

#### Test 7.2.2: Missing Indicator Data
```
Disable indicator calculation
Run task
Expected: Logs warning "No indicator data for {symbol}", skips symbol
```

**Checklist:**
- [ ] Graceful skip on missing indicators
- [ ] Error logged as warning

#### Test 7.2.3: Claude API Failure
```
Disable ANTHROPIC_API_KEY
Run task
Expected: Signals saved without Claude analysis
```

**Checklist:**
- [ ] Signals still generated
- [ ] Claude analysis skipped gracefully
- [ ] Error logged as warning (not error)
- [ ] indicators_used has no claude_analysis field

---

## ✅ Comparative Analysis Tests

### Test 8.1: Signal Agreement Matrix

For 10 BTCUSDT signals in last hour:

```sql
SELECT 
  created_at,
  (SELECT signal_type FROM trading_signals s2 
   WHERE s2.symbol = s1.symbol 
   AND s2.strategy = 'MOMENTUM' 
   AND s2.created_at = s1.created_at) as momentum,
  (SELECT signal_type FROM trading_signals s2 
   WHERE s2.symbol = s1.symbol 
   AND s2.strategy = 'CONTRARIAN' 
   AND s2.created_at = s1.created_at) as contrarian,
  (SELECT signal_type FROM trading_signals s2 
   WHERE s2.symbol = s1.symbol 
   AND s2.strategy = 'MACD' 
   AND s2.created_at = s1.created_at) as macd,
  (SELECT signal_type FROM trading_signals s2 
   WHERE s2.symbol = s1.symbol 
   AND s2.strategy = 'BOLLINGER_BAND' 
   AND s2.created_at = s1.created_at) as bb
FROM trading_signals s1
WHERE symbol = 'BTCUSDT'
  AND created_at > NOW() - INTERVAL '1 hour'
GROUP BY created_at
LIMIT 10;
```

Analyze:
- [ ] Patterns of signal convergence
- [ ] Divergence frequency
- [ ] Which signals align most often

---

## 📊 Summary Metrics

After all tests, collect:

```sql
SELECT 
  strategy,
  COUNT(*) as total_signals,
  SUM(CASE WHEN signal_type IN ('STRONG_BUY', 'BUY') THEN 1 ELSE 0 END) as buy_signals,
  SUM(CASE WHEN signal_type = 'HOLD' THEN 1 ELSE 0 END) as hold_signals,
  SUM(CASE WHEN signal_type IN ('SELL', 'STRONG_SELL') THEN 1 ELSE 0 END) as sell_signals,
  ROUND(AVG(signal_strength), 2) as avg_strength,
  ROUND(AVG(confidence), 2) as avg_confidence
FROM trading_signals
WHERE created_at > NOW() - INTERVAL '1 hour'
GROUP BY strategy;
```

**Expected:**
- [ ] ~1440 signals per strategy (1 per minute per watchlist)
- [ ] Distribution roughly 30% BUY, 40% HOLD, 30% SELL
- [ ] Average strength balanced around 50
- [ ] Average confidence 60-80

---

## 🎯 Final Sign-Off

**All Tests Passed:** ✅ YES / ⚠️ WITH ISSUES / ❌ NO

**Issues Found:**
```
(List any issues discovered during testing)
```

**Performance Summary:**
- Task duration: ___ seconds
- API cost: $___
- Database impact: ___ GB

**Ready for Production:** ✅ YES / 🟡 CONDITIONAL / ❌ NO

---

**Tested By:** [Your Name]  
**Test Date:** [Date]  
**Test Duration:** [Hours]
