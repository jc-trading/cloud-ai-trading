# P1 Test Execution Guide

**Date:** 2026-04-14  
**Phase:** Phase 4 - P1: Extended Signal Generation  
**Status:** READY TO EXECUTE

---

## 📋 Overview

This guide provides step-by-step instructions for executing all P1 tests. Follow this order:

1. **Setup** - Prepare environment
2. **Unit Tests** - Test signal generation logic
3. **Integration Tests** - Test full pipeline
4. **Performance Tests** - Measure execution metrics
5. **Data Validation** - Verify database output
6. **Error Handling** - Test edge cases
7. **Cleanup** - Document results

---

## 🔧 Setup

### Prerequisites
- Docker and docker-compose running
- Database connection working
- Python environment with pytest installed
- Celery workers running

### Start Services
```bash
# From repository root
docker compose up -d

# Wait for services to start
sleep 5

# Verify services
docker compose ps
# Expected: postgres, redis, celery-worker all running
```

### Install Test Dependencies
```bash
pip install pytest pytest-asyncio sqlalchemy
```

---

## ✅ Phase 1: Unit Tests (MACD Signal Generation)

### Test 1.1: Bullish MACD Crossover
```bash
# Run test
pytest tests/test_p1_signals.py::TestMACDSignalGeneration::test_macd_bullish_crossover -v

# Expected output:
# test_macd_bullish_crossover PASSED
# - signal_type = STRONG_BUY ✓
# - signal_strength = 100 ✓
# - confidence = 90 ✓
```

**Checklist:**
- [ ] Test passes
- [ ] Correctly identifies crossover
- [ ] Sets strength to 100
- [ ] Sets confidence to 90

---

### Test 1.2: Bearish MACD Crossover
```bash
pytest tests/test_p1_signals.py::TestMACDSignalGeneration::test_macd_bearish_crossover -v

# Expected: signal_type = STRONG_SELL, strength = 0, confidence = 90
```

**Checklist:**
- [ ] Test passes
- [ ] Correctly identifies bearish crossover
- [ ] Sets strength to 0
- [ ] Sets confidence to 90

---

### Test 1.3: MACD Bullish Trend (No Crossover)
```bash
pytest tests/test_p1_signals.py::TestMACDSignalGeneration::test_macd_bullish_trend_no_crossover -v

# Expected: signal_type = BUY, strength 60-75, confidence 70-80
```

**Checklist:**
- [ ] Test passes
- [ ] BUY signal when MACD > Signal
- [ ] Strength in range 60-75
- [ ] Confidence in range 70-80

---

## ✅ Phase 2: Unit Tests (Bollinger Band Signal Generation)

### Test 2.1: Upper Breakout
```bash
pytest tests/test_p1_signals.py::TestBollingerBandSignalGeneration::test_bb_upper_breakout -v

# Expected: signal_type = STRONG_BUY, strength = 100, confidence = 85
```

**Checklist:**
- [ ] Test passes
- [ ] STRONG_BUY when price > BB upper
- [ ] Strength = 100
- [ ] Confidence = 85

---

### Test 2.2: Lower Breakout
```bash
pytest tests/test_p1_signals.py::TestBollingerBandSignalGeneration::test_bb_lower_breakout -v

# Expected: signal_type = STRONG_SELL, strength = 0, confidence = 85
```

**Checklist:**
- [ ] Test passes
- [ ] STRONG_SELL when price < BB lower
- [ ] Strength = 0
- [ ] Confidence = 85

---

### Test 2.3: Price Near Upper Band
```bash
pytest tests/test_p1_signals.py::TestBollingerBandSignalGeneration::test_bb_price_near_upper_band -v

# Expected: signal_type = BUY, strength 65-75, confidence = 75
```

**Checklist:**
- [ ] Test passes
- [ ] BUY when approaching upper band
- [ ] Strength in range 65-75
- [ ] Confidence = 75

---

## ✅ Phase 3: Integration Tests (All 4 Signals)

### Test 3.1: All 4 Signals Generated Per Symbol

**Manual Test Steps:**

1. Start fresh database session:
```bash
# Connect to database
docker compose exec postgres psql -U cloudaitrading -d cloudaitrading_db
```

2. Clear recent signals:
```sql
DELETE FROM trading_signals WHERE created_at > NOW() - INTERVAL '1 hour';
```

3. Trigger signal generation:
```bash
# Option 1: Trigger via API
curl -X POST http://localhost:8000/api/signals/generate

# Option 2: Trigger Celery task directly
docker compose exec celery-worker celery -A tasks.celery_app call generate_trading_signals

# Option 3: Wait for scheduled task (runs every minute)
```

4. Verify all 4 signals generated:
```sql
SELECT strategy, COUNT(*) as signal_count
FROM trading_signals
WHERE symbol = 'BTCUSDT'
  AND created_at > NOW() - INTERVAL '5 minutes'
GROUP BY strategy
ORDER BY strategy;

-- Expected output:
-- BOLLINGER_BAND | 1
-- CONTRARIAN     | 1
-- MACD           | 1
-- MOMENTUM       | 1
```

**Checklist:**
- [ ] 4 signals generated per symbol
- [ ] All 4 strategies present (MOMENTUM, CONTRARIAN, MACD, BOLLINGER_BAND)
- [ ] No duplicate signals
- [ ] Timestamps consistent

---

### Test 3.2: Claude AI Receives All 4 Signals

**Manual Test Steps:**

1. Query Claude analysis data:
```sql
SELECT 
  symbol,
  created_at,
  indicators_used->'claude_analysis'->'all_signals'->>'momentum' as momentum_signal,
  indicators_used->'claude_analysis'->'all_signals'->>'contrarian' as contrarian_signal,
  indicators_used->'claude_analysis'->'all_signals'->>'macd' as macd_signal,
  indicators_used->'claude_analysis'->'all_signals'->>'bollinger_band' as bb_signal
FROM trading_signals
WHERE indicators_used->'claude_analysis' IS NOT NULL
  AND created_at > NOW() - INTERVAL '10 minutes'
LIMIT 5;
```

2. Inspect Claude analysis structure:
```sql
SELECT
  symbol,
  indicators_used->'claude_analysis'->>'action' as action,
  indicators_used->'claude_analysis'->>'confidence' as confidence,
  indicators_used->'claude_analysis'->>'reason' as reason
FROM trading_signals
WHERE indicators_used->'claude_analysis' IS NOT NULL
LIMIT 3;
```

**Checklist:**
- [ ] All 4 signals present in Claude analysis
- [ ] Signal types correct (BUY, SELL, HOLD, STRONG_BUY, STRONG_SELL)
- [ ] Strength values within 0-100
- [ ] Confidence values within 0-100
- [ ] Claude analysis includes "convergence" or "divergence" language

---

### Test 3.3: Signal Convergence Analysis

**Manual Test Steps:**

1. Query signal convergence matrix:
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
ORDER BY created_at DESC
LIMIT 10;
```

2. Analyze patterns:
```
All 4 BUY → HIGH convergence (Claude should recommend BUY/STRONG_BUY with high confidence)
3 BUY, 1 HOLD → MEDIUM convergence
2 BUY, 2 SELL → LOW convergence (Claude should recommend HOLD with low confidence)
All 4 SELL → HIGH convergence (Claude should recommend SELL/STRONG_SELL)
```

**Checklist:**
- [ ] Convergence patterns identified
- [ ] Claude confidence reflects convergence (higher with agreement)
- [ ] Divergence cases show caution in recommendations
- [ ] Signal strength values reflect convergence

---

## ✅ Phase 4: Performance Tests

### Test 4.1: Celery Task Execution Time

**Manual Test Steps:**

1. Monitor task execution:
```bash
# Watch Celery logs
docker compose logs -f celery-worker | grep "generate_trading_signals"
```

2. Run task and time it:
```bash
# Terminal 1: Watch logs
docker compose logs -f celery-worker

# Terminal 2: Trigger task
time curl -X POST http://localhost:8000/api/signals/generate
```

3. Record timing:
- Expected: < 10 seconds (increased from P0's 5-7 seconds)
- Acceptable: 6-8 seconds with 2 new signals

**Checklist:**
- [ ] Task completes < 10 seconds
- [ ] No timeout errors
- [ ] Performance degradation < 20% from P0
- [ ] Consistent timing across runs

---

### Test 4.2: Database Query Performance

**Manual Test Steps:**

1. Connect to database:
```bash
docker compose exec postgres psql -U cloudaitrading -d cloudaitrading_db
```

2. Analyze MACD query performance:
```sql
EXPLAIN ANALYZE
SELECT * FROM trading_signals
WHERE strategy = 'MACD'
  AND created_at > NOW() - INTERVAL '1 hour'
  AND signal_type = 'STRONG_BUY';
```

3. Expected output format:
```
Seq Scan on trading_signals
  Filter: (strategy = 'MACD' AND signal_type = 'STRONG_BUY' AND ...)
  Execution Time: < 100ms
```

4. Check for index usage:
```sql
-- If using Bitmap Index Scan instead of Seq Scan, indexes are working
CREATE INDEX idx_trading_signals_strategy_timestamp 
ON trading_signals(strategy, created_at DESC);

-- Retry query
EXPLAIN ANALYZE ...
```

**Checklist:**
- [ ] Queries execute < 100ms
- [ ] Indexes being used (Bitmap/Index Scan, not Seq Scan)
- [ ] No query plan changes from P0

---

### Test 4.3: Memory Usage

**Manual Test Steps:**

1. Baseline memory before task:
```bash
docker stats cloudaitrading_celery-worker --no-stream
# Note: MEM USAGE value
```

2. Trigger task and monitor:
```bash
# Terminal 1: Continuous monitoring
docker stats cloudaitrading_celery-worker --interval 1

# Terminal 2: Trigger task
curl -X POST http://localhost:8000/api/signals/generate
```

3. Check memory after task:
```bash
docker stats cloudaitrading_celery-worker --no-stream
```

4. Calculate delta:
- Memory increase should be < 500MB
- Should return to baseline after task completes

**Checklist:**
- [ ] Memory usage reasonable (< 500MB increase)
- [ ] No memory leaks over multiple task runs
- [ ] Memory returns to baseline after task completes

---

## ✅ Phase 5: Data Validation Tests

### Test 5.1: MACD Signal Strength Calculation

**Manual Test Steps:**

1. Query MACD signals:
```sql
SELECT 
  symbol,
  signal_type,
  signal_strength,
  indicators_used->>'MACD' as macd_value,
  indicators_used->>'MACD_Signal' as macd_signal_value
FROM trading_signals
WHERE strategy = 'MACD'
  AND created_at > NOW() - INTERVAL '10 minutes'
ORDER BY created_at DESC
LIMIT 5;
```

2. Verify each signal:
```
For STRONG_BUY: signal_strength should = 100
For STRONG_SELL: signal_strength should = 0
For BUY: signal_strength should be 60-75
For SELL: signal_strength should be 25-40
For HOLD: signal_strength should be ~50
All values should be 0-100
```

**Checklist:**
- [ ] STRONG_BUY signals have strength = 100
- [ ] STRONG_SELL signals have strength = 0
- [ ] BUY signals have reasonable strength (60-75)
- [ ] SELL signals have reasonable strength (25-40)
- [ ] All values within 0-100 range
- [ ] Indicators include MACD and MACD_Signal values

---

### Test 5.2: Bollinger Band Signal Consistency

**Manual Test Steps:**

1. Query BB signals:
```sql
SELECT 
  symbol,
  signal_type,
  signal_strength,
  indicators_used->>'Price' as price,
  indicators_used->>'BB_Upper' as bb_upper,
  indicators_used->>'BB_Lower' as bb_lower
FROM trading_signals
WHERE strategy = 'BOLLINGER_BAND'
  AND created_at > NOW() - INTERVAL '10 minutes'
ORDER BY created_at DESC
LIMIT 5;
```

2. Verify consistency:
```
If price > BB_Upper: signal should be STRONG_BUY, strength = 100
If price < BB_Lower: signal should be STRONG_SELL, strength = 0
If price between bands: signal should be HOLD or BUY/SELL based on proximity
Signal strength should increase with distance from bands
```

**Checklist:**
- [ ] Breakout signals have correct strength values
- [ ] Price proximity detected correctly
- [ ] Signal strength increases with distance from bands
- [ ] All required indicators present (Price, BB_Upper, BB_Lower, BB_Middle)

---

## ✅ Phase 6: Telegram Notification Tests

### Test 6.1: STRONG Signal Notifications

**Manual Test Steps:**

1. Ensure Telegram bot is configured:
```bash
# Check environment variable
echo $TELEGRAM_BOT_TOKEN
echo $TELEGRAM_CHAT_ID
```

2. Wait for or generate a STRONG signal:
```bash
# Option 1: Monitor for next strong signal (watch logs)
docker compose logs -f celery-worker | grep "STRONG"

# Option 2: Manually create test signal (if test data available)
```

3. Check Telegram message:
- Expected message includes:
  - [ ] Symbol (e.g., BTCUSDT)
  - [ ] Signal type (STRONG_BUY or STRONG_SELL)
  - [ ] All 4 signal types mentioned
  - [ ] Claude confidence level
  - [ ] Entry price
  - [ ] Stop loss
  - [ ] Take profit
  - [ ] Message is readable and properly formatted

4. Verify message structure:
```
Expected format:
🚀 BTCUSDT STRONG_BUY
Confidence: 85%

📊 Signals:
- Momentum: BUY (70%)
- Contrarian: HOLD (50%)
- MACD: STRONG_BUY (100%)
- BB: BUY (68%)

💰 Entry: 42750
🛑 Stop Loss: 42500
🎯 Take Profit: 43200

📝 Analysis: [Claude reasoning]
```

**Checklist:**
- [ ] Notification sends to Telegram
- [ ] Includes symbol
- [ ] Includes all 4 signal types
- [ ] Includes Claude recommendation
- [ ] Includes entry/exit prices
- [ ] Message is readable and well-formatted

---

## ✅ Phase 7: Celery Task Tests

### Test 7.1: Task Completes Without Error

**Manual Test Steps:**

1. Watch Celery worker logs:
```bash
docker compose logs -f celery-worker
```

2. Wait for or trigger next task run:
```bash
# Trigger manually
docker compose exec celery-worker celery -A tasks.celery_app call generate_trading_signals
```

3. Check logs for:
```
✓ No ERROR logs
✓ No CRITICAL logs
✓ Message: "Processing X active watchlists"
✓ Message: "Signal generated for {symbol}: ..."
✓ Task completes with status: "success"
```

4. Verify database:
```sql
SELECT COUNT(*) as total_signals
FROM trading_signals
WHERE created_at > NOW() - INTERVAL '5 minutes';
-- Should show 4 signals per symbol
```

**Checklist:**
- [ ] Task completes successfully
- [ ] No error logs in Celery
- [ ] All signals saved to database
- [ ] Claude analysis attempted (if API key set)
- [ ] Task status: SUCCESS

---

### Test 7.2: Error Handling

#### Test 7.2.1: No Candle Data
```bash
# Temporarily update test data
docker compose exec postgres psql -U cloudaitrading -d cloudaitrading_db

-- Delete recent candles
DELETE FROM ohlcv_candles WHERE created_at > NOW() - INTERVAL '1 hour';

-- Trigger task
```

**Checklist:**
- [ ] Task logs warning: "No candle data for {symbol}"
- [ ] Symbol is skipped gracefully
- [ ] Task continues processing other symbols
- [ ] No fatal errors

---

#### Test 7.2.2: Missing Indicator Data
```bash
-- Delete recent indicators
DELETE FROM technical_indicators WHERE created_at > NOW() - INTERVAL '1 hour';

-- Trigger task
```

**Checklist:**
- [ ] Task logs warning: "No indicator data for {symbol}"
- [ ] Symbol is skipped gracefully
- [ ] Task continues processing
- [ ] No fatal errors

---

#### Test 7.2.3: Claude API Failure
```bash
# Unset API key
export ANTHROPIC_API_KEY=""

# Restart Celery
docker compose restart celery-worker

# Trigger task
```

**Checklist:**
- [ ] Signals still generated without Claude
- [ ] Claude analysis skipped gracefully
- [ ] Task logs warning (not error)
- [ ] Signals saved without claude_analysis field
- [ ] Task completes successfully

---

## ✅ Phase 8: Comparative Analysis

### Test 8.1: Signal Agreement Matrix

**Manual Test Steps:**

1. Query signal agreement:
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

2. Analyze results:
```
Count rows where:
- All 4 = BUY/STRONG_BUY (HIGH convergence)
- All 4 = SELL/STRONG_SELL (HIGH convergence)
- 3 = BUY, 1 = HOLD (MEDIUM convergence)
- Mixed signals (LOW convergence)
```

**Checklist:**
- [ ] Convergence patterns identified
- [ ] Divergence frequency noted
- [ ] Which signals align most often documented
- [ ] Patterns make sense given market conditions

---

## 📊 Summary & Sign-Off

### Collect Summary Metrics

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
GROUP BY strategy
ORDER BY strategy;
```

### Document Results

```markdown
# P1 Testing Results

Date: [TODAY]
Tester: [YOUR_NAME]
Duration: [HOURS_SPENT]

## Unit Tests
- [ ] MACD Bullish Crossover: PASS
- [ ] MACD Bearish Crossover: PASS
- [ ] MACD Trend: PASS
- [ ] BB Upper Breakout: PASS
- [ ] BB Lower Breakout: PASS
- [ ] BB Proximity: PASS

## Integration Tests
- [ ] All 4 signals generated: PASS
- [ ] Claude receives all 4: PASS
- [ ] Convergence analysis: PASS

## Performance Tests
- [ ] Task execution time: PASS (___ seconds)
- [ ] Database queries: PASS (< 100ms)
- [ ] Memory usage: PASS (< 500MB increase)

## Data Validation
- [ ] MACD signal strength: PASS
- [ ] BB signal consistency: PASS
- [ ] Signal convergence matrix: PASS

## Telegram Tests
- [ ] Notifications send: PASS
- [ ] Format correct: PASS
- [ ] All data included: PASS

## Error Handling
- [ ] Missing candle data: PASS
- [ ] Missing indicators: PASS
- [ ] Claude API failure: PASS

## Issues Found
(List any issues, or "None")

## Ready for Production
✅ YES / 🟡 WITH NOTES / ❌ NO
```

---

## 🚀 Next Steps

After all tests pass:

1. **Create Code Review Documentation**
   - File: `/docs/code-review/P1-TESTING_RESULTS.md`
   - Document any issues found and fixes applied
   - Note performance metrics

2. **Update Status Files**
   - Update `/docs/README.md` to mark P1 as COMPLETE
   - Update `/docs/implementation/P1-*.md` with final status

3. **Merge to Production**
   - Create git commit with all test results
   - Deploy to staging for final verification
   - Deploy to production

4. **Begin P2 Planning**
   - Next phase: Connect QuantStrategy to signal generation
   - Update `/docs/implementation/P2-*.md` with next features

---

## 📞 Support

If tests fail:
1. Check logs: `docker compose logs celery-worker`
2. Check database connectivity
3. Verify all 4 signal methods are implemented
4. Review code changes in `signals.py`, `trading_tasks.py`, `claude.py`

---

**Test Execution Status:** ⏳ READY TO START  
**Last Updated:** 2026-04-14  
**Estimated Duration:** 2-4 hours
