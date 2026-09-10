# P0 Testing Checklist

**Phase:** Phase 4 - P0: Claude AI Celery Integration  
**Date:** 2026-04-14  
**Status:** 🟡 READY FOR TESTING  

---

## 🧪 Pre-Testing Setup

### Prerequisites
- [ ] ANTHROPIC_API_KEY configured in `.env` or environment
- [ ] Claude API quota available (check API usage dashboard)
- [ ] PostgreSQL database running
- [ ] Redis running
- [ ] Celery worker running: `docker compose up celery-worker`
- [ ] Celery beat running: `docker compose up celery-beat`

### Preparation
```bash
# 1. Verify database is clean
docker compose exec postgres psql -U postgres -d cloudaitrading -c "SELECT COUNT(*) FROM trading_signals;"

# 2. Verify Celery workers are running
docker compose logs celery-worker

# 3. Check API key is set
echo $ANTHROPIC_API_KEY

# 4. Clear any existing test data (optional)
docker compose exec postgres psql -U postgres -d cloudaitrading -c "DELETE FROM trading_signals WHERE created_at > NOW() - INTERVAL '1 hour';"
```

---

## ✅ Phase 1: Basic Signal Generation (Without Claude)

### Test 1.1: Verify Rule-Based Signals Still Work
```python
# Run the signal generation task manually
# Expected: 2 signals per symbol (momentum + contrarian)

from app.tasks.trading_tasks import generate_trading_signals
result = generate_trading_signals()
# Should return: {"status": "success"}
```

**Checklist:**
- [ ] Task completes without error
- [ ] Signals appear in database
- [ ] Signal types are STRONG_BUY/BUY/HOLD/SELL/STRONG_SELL
- [ ] Signal strength is 0-100
- [ ] Signal confidence is 0-100
- [ ] Telegram notification sent (if STRONG signal)

---

## ✅ Phase 2: Claude API Integration Testing

### Test 2.1: Claude API is Called
```sql
-- Check if Claude analysis data is in database
SELECT 
  symbol, 
  signal_type, 
  confidence,
  indicators_used->>'claude_analysis' as claude_data,
  created_at
FROM trading_signals
WHERE created_at > NOW() - INTERVAL '5 minutes'
ORDER BY created_at DESC
LIMIT 5;
```

**Expected Output:**
```
symbol | signal_type | confidence | claude_data | created_at
-------|-------------|------------|-----------|----------
BTCUSDT| STRONG_BUY  | 78         | {...}     | 2026-04-14 12:34:56
```

**Checklist:**
- [ ] Claude analysis JSON is stored
- [ ] Claude action is present (BUY/SELL/HOLD)
- [ ] Claude confidence is present (0-100)
- [ ] Entry/exit prices are present
- [ ] Risk warning is present
- [ ] API cost is logged

### Test 2.2: Claude Confidence vs Rule Confidence
```sql
-- Compare rule-based vs Claude confidence
SELECT 
  symbol,
  signal_type,
  signal_strength,
  confidence,
  indicators_used->'claude_analysis'->>'confidence' as claude_confidence
FROM trading_signals
WHERE indicators_used->'claude_analysis' IS NOT NULL
AND created_at > NOW() - INTERVAL '10 minutes'
LIMIT 10;
```

**Expected:** 
- Confidence values should differ (rule-based is fixed, Claude is dynamic)
- Claude confidence should be in range 0-100

**Checklist:**
- [ ] Confidence values are different from rule-based defaults
- [ ] Claude confidence ranges appropriately
- [ ] Confidence aligns with signal strength

### Test 2.3: API Cost Logging
```bash
# Check Docker logs for cost information
docker compose logs celery-worker | grep "Claude analysis for"
```

**Expected Output:**
```
Claude analysis for BTCUSDT: action=BUY, confidence=78, tokens=500, cost=$0.0039
Claude analysis for ETHUSDT: action=HOLD, confidence=55, tokens=520, cost=$0.0041
```

**Checklist:**
- [ ] Cost is logged for each Claude analysis
- [ ] Tokens used are logged
- [ ] Cost is in expected range ($0.001-0.005)

---

## ✅ Phase 3: Error Handling & Fallback

### Test 3.1: Graceful Fallback (Remove API Key)
```bash
# Temporarily unset API key for the worker and recreate it
ANTHROPIC_API_KEY= docker compose up -d --force-recreate celery-worker

# Check that signals are still generated
docker compose logs celery-worker | grep -Ei "warning|error|claude"

# Restore the worker with the normal environment after this test
docker compose up -d --force-recreate celery-worker
```

**Expected:**
- Signal generation continues without Claude
- Warning logged: "Claude API key not configured"
- Signals saved with rule-based data only

**Checklist:**
- [ ] Signal generation continues (no crash)
- [ ] Error is logged as WARNING (not ERROR)
- [ ] Signal saved to database
- [ ] `indicators_used` has no `claude_analysis`

### Test 3.2: Graceful Fallback (API Timeout)
```bash
# This simulates API timeout by temporarily disabling network
# (Use Docker networking features or patch ANTHROPIC_API_KEY to an invalid value)

# Or just check logs when Claude API is slow
docker compose logs celery-worker | grep -i timeout
```

**Expected:**
- Signal generation completes even if Claude times out
- Fallback to rule-based signal
- Error logged: "Claude AI analysis failed for {symbol}"

**Checklist:**
- [ ] Signal generation completes (no Celery task failure)
- [ ] Error is logged as WARNING
- [ ] Signal uses rule-based confidence
- [ ] No data corruption

---

## ✅ Phase 4: Performance Testing

### Test 4.1: Task Execution Time
```bash
# Monitor Celery task duration
docker compose logs celery-worker | grep "Task.*started\|Task.*succeeded"

# Or use Celery flower UI (if available)
# Access: http://localhost:5555
```

**Expected:**
- Task duration depends on symbol count and Claude latency
- Per-symbol Claude call is capped by the 15s client timeout
- Other Celery workers can continue; this task processes symbols sequentially

**Checklist:**
- [ ] Task duration is acceptable for the current watchlist size
- [ ] Timeout errors fall back to rule-based signals
- [ ] Redis queue not overflowing
- [ ] Database connections normal

### Test 4.2: Database Query Performance
```sql
-- Check if new indexes needed
EXPLAIN ANALYZE
SELECT * FROM trading_signals
WHERE created_at > NOW() - INTERVAL '1 hour'
AND indicators_used ? 'claude_analysis'
ORDER BY created_at DESC;
```

**Expected:**
- Query executes acceptably for the current table size
- Sequential scan is acceptable on small local tables
- If this becomes slow, add a GIN/JSONB index in a migration before requiring index use

**Checklist:**
- [ ] JSON queries are performant for current data volume
- [ ] No slow query logs
- [ ] Database CPU normal

---

## ✅ Phase 5: Telegram Notification Testing

### Test 5.1: Notification Includes Claude Data
```bash
# Check Telegram message format
# Expected message includes:
# - Symbol
# - Signal type (rule-based)
# - Confidence (updated by Claude)
# - Recommendation (from Claude)
```

**Checklist:**
- [ ] Telegram notification sends
- [ ] Message includes symbol
- [ ] Message includes updated confidence
- [ ] Message includes Claude recommendation
- [ ] Message is readable and formatted well

### Test 5.2: STRONG Signals Trigger Notifications
```sql
-- Check that STRONG signals are sent
SELECT 
  symbol,
  signal_type,
  created_at,
  indicators_used->'claude_analysis'->>'confidence' as confidence
FROM trading_signals
WHERE signal_type IN ('STRONG_BUY', 'STRONG_SELL')
AND created_at > NOW() - INTERVAL '1 hour'
ORDER BY created_at DESC;
```

**Checklist:**
- [ ] STRONG_BUY signals trigger notification
- [ ] STRONG_SELL signals trigger notification
- [ ] BUY/SELL signals don't trigger (unless configured)

---

## ✅ Phase 6: Data Validation

### Test 6.1: Claude Response Structure
```sql
-- Verify all required Claude fields are present
SELECT 
  indicators_used->'claude_analysis' as claude_data,
  (indicators_used->'claude_analysis'->>'action') as action,
  (indicators_used->'claude_analysis'->>'confidence') as confidence,
  (indicators_used->'claude_analysis'->>'entry_price') as entry_price,
  (indicators_used->'claude_analysis'->>'stop_loss') as stop_loss,
  (indicators_used->'claude_analysis'->>'take_profit') as take_profit
FROM trading_signals
WHERE indicators_used->'claude_analysis' IS NOT NULL
LIMIT 5;
```

**Expected:**
- All 11 fields present (action, confidence, reason, entry_price, stop_loss, take_profit, risk_reward_ratio, key_factors, risk_warning, tokens_used, api_cost)
- No NULL critical fields
- Data types are correct

**Checklist:**
- [ ] action field is present and valid (BUY/SELL/HOLD)
- [ ] confidence field is numeric (0-100)
- [ ] entry_price/stop_loss/take_profit are numeric or null
- [ ] key_factors is array
- [ ] risk_warning is string

### Test 6.2: No Data Corruption
```sql
-- Verify existing signals are not corrupted
SELECT 
  id,
  signal_type,
  signal_strength,
  confidence,
  indicators_used,
  created_at
FROM trading_signals
WHERE created_at < NOW() - INTERVAL '1 hour'
LIMIT 10;
```

**Checklist:**
- [ ] Old signals are unchanged
- [ ] No NULL values in critical fields
- [ ] Timestamps are correct
- [ ] No duplicate signals

---

## ✅ Phase 7: Cost & Quota Monitoring

### Test 7.1: API Cost Tracking
```bash
# Sum monthly estimated cost
docker compose logs celery-worker | grep "cost=" | awk -F'cost=\\$' '{sum += $2} END {print "Total: $" sum}'
```

**Expected:**
- Verify costs match pricing: ~$0.001-0.005 per signal
- Estimate for 1,440 analyses/day at ~$0.0039 each = ~$5.60/day

**Checklist:**
- [ ] Costs are being tracked
- [ ] Costs are within budget
- [ ] No unexpected charges
- [ ] API quota not approaching limits

### Test 7.2: Token Usage Tracking
```sql
-- Sum tokens used
SELECT 
  SUM(CAST(indicators_used->'claude_analysis'->>'tokens_used' AS INT)) as total_tokens,
  COUNT(*) as signal_count,
  ROUND(SUM(CAST(indicators_used->'claude_analysis'->>'tokens_used' AS INT))::NUMERIC / COUNT(*), 2) as avg_tokens_per_signal
FROM trading_signals
WHERE indicators_used->'claude_analysis' IS NOT NULL
AND created_at > NOW() - INTERVAL '1 hour';
```

**Expected:**
- Average tokens per signal: 400-600
- Total tokens reasonable for cost

**Checklist:**
- [ ] Token usage is being tracked
- [ ] Token usage per signal is consistent
- [ ] No unexpected token spikes

---

## 🐛 Troubleshooting Guide

### Issue: Signals not getting Claude analysis
**Diagnosis:**
```bash
docker compose logs celery-worker | grep -i "claude\|analysis"
```

**Solutions:**
1. Verify ANTHROPIC_API_KEY is set
2. Check Claude API status page
3. Verify network connectivity
4. Check API rate limits not exceeded

### Issue: Celery task times out
**Diagnosis:**
```bash
docker compose logs celery-worker | grep -i "timeout\|time limit"
```

**Solutions:**
1. Increase Celery task timeout in settings
2. Reduce number of symbols being analyzed
3. Check for slow database queries
4. Check for network latency to Claude API

### Issue: Database queries are slow
**Diagnosis:**
```sql
EXPLAIN ANALYZE SELECT * FROM trading_signals WHERE indicators_used ? 'claude_analysis';
```

**Solutions:**
1. Add indexes on JSON fields if needed
2. Consider partitioning large tables
3. Increase max_connections if needed
4. Check disk space

---

## ✅ Final Verification

- [ ] All Phase 1-7 tests passed
- [ ] No error logs in Celery worker
- [ ] No error logs in FastAPI backend
- [ ] No error logs in PostgreSQL
- [ ] Signals visible in frontend (when complete)
- [ ] Telegram notifications working
- [ ] Cost is within budget
- [ ] Performance is acceptable

---

## 📊 Sign-Off

**Tested By:** [Your Name]  
**Date Tested:** [Date]  
**Result:** ✅ PASSED / ⚠️ ISSUES / ❌ FAILED  

**Issues Found:**
- [ ] No issues found
- [ ] Minor issues (list below)
- [ ] Critical issues (list below)

**Notes:**
```
(Add any notes or observations)
```

---

**Status After Testing:** 🟢 READY FOR PRODUCTION / 🟡 MINOR FIXES NEEDED / 🔴 BLOCKED
