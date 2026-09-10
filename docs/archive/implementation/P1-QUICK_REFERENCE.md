═══════════════════════════════════════════════════════════════════════════════
P1 TESTING QUICK REFERENCE CARD
Extended Signal Generation (MACD + Bollinger Band)
═══════════════════════════════════════════════════════════════════════════════

START HERE: /docs/implementation/P1-TESTING_SUMMARY.md

───────────────────────────────────────────────────────────────────────────────
PHASE 1: UNIT TESTS (5 MINUTES)
───────────────────────────────────────────────────────────────────────────────

$ pytest tests/test_p1_signals.py -v

Expected Result: 12/12 tests pass ✓

Tests:
  - 5 MACD signal generation tests
  - 6 Bollinger Band signal generation tests
  - 1 signal structure consistency test

Status: ✓ PASS = Proceed to Phase 2
        ✗ FAIL = Fix code issues first

───────────────────────────────────────────────────────────────────────────────
PHASE 2: SETUP (10 MINUTES)
───────────────────────────────────────────────────────────────────────────────

$ docker compose up -d
$ sleep 5
$ docker compose ps

Expected: postgres, redis, celery-worker all UP ✓

───────────────────────────────────────────────────────────────────────────────
PHASE 3: INTEGRATION TESTS (1 HOUR)
───────────────────────────────────────────────────────────────────────────────

Resource: /docs/implementation/P1-TEST_EXECUTION_GUIDE.md (Sections 3-8)

Step 1: Trigger Signal Generation
  $ curl -X POST http://localhost:8000/api/signals/generate

Step 2: Verify All 4 Signals
  $ docker compose exec postgres psql -U cloudaitrading -d cloudaitrading_db
  > SELECT strategy, COUNT(*) FROM trading_signals
    WHERE created_at > NOW() - INTERVAL '5 minutes'
    GROUP BY strategy;

  Expected:
    BOLLINGER_BAND | 1
    CONTRARIAN     | 1
    MACD           | 1
    MOMENTUM       | 1

Step 3: Verify Claude Gets All 4
  > SELECT indicators_used->'claude_analysis'->'all_signals'->>'momentum' as m,
           indicators_used->'claude_analysis'->'all_signals'->>'macd' as macd
    FROM trading_signals
    WHERE indicators_used->'claude_analysis' IS NOT NULL LIMIT 1;

  Expected: Both fields populated (not null)

Status: ✓ All 4 signals = Ready for Phase 4

───────────────────────────────────────────────────────────────────────────────
PHASE 4: PERFORMANCE TESTS (30 MINUTES)
───────────────────────────────────────────────────────────────────────────────

Test 4.1: Task Execution Time
  Terminal 1: $ docker compose logs -f celery-worker
  Terminal 2: $ time curl -X POST http://localhost:8000/api/signals/generate

  Expected: < 10 seconds (baseline: 6-8 seconds)

Test 4.2: Database Query Performance
  $ docker compose exec postgres psql -U cloudaitrading -d cloudaitrading_db
  > EXPLAIN ANALYZE SELECT * FROM trading_signals
    WHERE strategy = 'MACD'
      AND created_at > NOW() - INTERVAL '1 hour';

  Expected: Execution Time: < 100ms

Test 4.3: Memory Usage
  $ docker stats cloudaitrading_celery-worker --no-stream

  Expected: Memory increase < 500MB

───────────────────────────────────────────────────────────────────────────────
PHASE 5: DATA VALIDATION (20 MINUTES)
───────────────────────────────────────────────────────────────────────────────

Resource: /tests/p1_validation_queries.sql

Copy/paste each section into psql and verify results:

Section 2.1: MACD signals by type
  > SELECT signal_type, COUNT(*) FROM trading_signals
    WHERE strategy = 'MACD'
      AND created_at > NOW() - INTERVAL '1 hour'
    GROUP BY signal_type;

  Expected: STRONG_BUY/BUY/SELL/STRONG_SELL present

Section 3.1: BB signals by type
  > SELECT signal_type, COUNT(*) FROM trading_signals
    WHERE strategy = 'BOLLINGER_BAND'
      AND created_at > NOW() - INTERVAL '1 hour'
    GROUP BY signal_type;

  Expected: STRONG_BUY/BUY/SELL/STRONG_SELL present

Section 9: Data Integrity Checks
  > SELECT COUNT(CASE WHEN signal_strength < 0
                       OR signal_strength > 100 THEN 1 END) as invalid
    FROM trading_signals
    WHERE created_at > NOW() - INTERVAL '1 hour';

  Expected: 0 (all values 0-100)

───────────────────────────────────────────────────────────────────────────────
PHASE 6: ERROR HANDLING (10 MINUTES)
───────────────────────────────────────────────────────────────────────────────

Test: Claude API Failure
  $ unset ANTHROPIC_API_KEY
  $ docker compose restart celery-worker
  $ curl -X POST http://localhost:8000/api/signals/generate
  $ docker compose logs celery-worker | grep -i claude

  Expected: Warning logged, signals still generated

───────────────────────────────────────────────────────────────────────────────
PHASE 7: FINAL SIGN-OFF (15 MINUTES)
───────────────────────────────────────────────────────────────────────────────

Document Results:
  Location: /docs/code-review/P1-TESTING_RESULTS.md

  Template:
    ✅ Unit tests: 12/12 pass
    ✅ All 4 signals generated
    ✅ Claude receives all 4 signals
    ✅ Signal strength 0-100
    ✅ Task execution time: __ seconds
    ✅ Database queries: < 100ms
    ✅ Error handling works

    Issues Found: [None / List any]
    Ready for Production: ✅ YES

Update Status:
  $ git status
  $ git add -A
  $ git commit -m "P1: Extended Signals - Testing complete"

───────────────────────────────────────────────────────────────────────────────
KEY VALIDATION QUERIES (Copy/Paste)
───────────────────────────────────────────────────────────────────────────────

1. All 4 signals present?
   SELECT DISTINCT strategy FROM trading_signals
   WHERE created_at > NOW() - INTERVAL '10 minutes'
   ORDER BY strategy;
   -- Expected: 4 rows

2. MACD signal strength correct?
   SELECT signal_type, MIN(signal_strength), MAX(signal_strength)
   FROM trading_signals WHERE strategy = 'MACD'
   GROUP BY signal_type;
   -- Expected: STRONG_BUY=100, STRONG_SELL=0

3. BB breakouts detected?
   SELECT COUNT(*) FROM trading_signals
   WHERE strategy = 'BOLLINGER_BAND'
     AND signal_type IN ('STRONG_BUY', 'STRONG_SELL');
   -- Expected: > 0

4. Claude gets all 4?
   SELECT COUNT(*) FROM trading_signals
   WHERE indicators_used->'claude_analysis'->'all_signals'->>'momentum' IS NOT NULL
     AND indicators_used->'claude_analysis'->'all_signals'->>'macd' IS NOT NULL;
   -- Expected: > 0

5. Signal strength always 0-100?
   SELECT COUNT(*) FROM trading_signals
   WHERE signal_strength NOT BETWEEN 0 AND 100;
   -- Expected: 0

───────────────────────────────────────────────────────────────────────────────
COMMON ISSUES & FIXES
───────────────────────────────────────────────────────────────────────────────

No signals generated?
  → Check watchlist is active
  → Verify candle data exists: SELECT COUNT(*) FROM ohlcv_candles;
  → Check Celery logs: docker compose logs celery-worker

Claude analysis missing?
  → Verify API key: echo $ANTHROPIC_API_KEY
  → Check Celery logs for "Claude API error"
  → System still generates signals without Claude

Database slow?
  → Create index: CREATE INDEX idx_trading_signals_strategy_timestamp
                  ON trading_signals(strategy, created_at DESC);
  → Check query plan: EXPLAIN ANALYZE

Memory spike?
  → Normal during signal generation
  → Should return to baseline after task
  → Restart worker if not: docker compose restart celery-worker

───────────────────────────────────────────────────────────────────────────────
DATABASE ACCESS
───────────────────────────────────────────────────────────────────────────────

Connect to database:
  $ docker compose exec postgres psql -U cloudaitrading -d cloudaitrading_db

Run SQL validation queries:
  $ docker compose exec postgres psql -U cloudaitrading -d cloudaitrading_db \
    < tests/p1_validation_queries.sql

View logs:
  $ docker compose logs -f celery-worker
  $ docker compose logs -f postgres

═══════════════════════════════════════════════════════════════════════════════
TIMELINE: ~2.5 hours total
  - Setup + Unit Tests: 15 min
  - Integration Tests: 1 hour
  - Performance Tests: 30 min
  - Data Validation: 20 min
  - Documentation: 15 min

NEXT STEPS (After all tests pass):
  1. Create /docs/code-review/P1-TESTING_RESULTS.md
  2. Update /docs/README.md - Mark P1 as COMPLETE
  3. Commit: git commit -m "P1: All tests passing"
  4. Deploy to production
  5. Begin P2 planning

═══════════════════════════════════════════════════════════════════════════════
Resources: /tests/P1_TESTING_INDEX.md | /docs/implementation/P1-TEST_EXECUTION_GUIDE.md
Status: READY TO EXECUTE | Created: 2026-04-14
═══════════════════════════════════════════════════════════════════════════════
