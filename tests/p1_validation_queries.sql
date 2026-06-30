-- P1 Validation Queries
-- Use these SQL queries to validate P1 implementation
-- Run against: cloudaitrading_db

-- ================================================================
-- SECTION 1: BASIC SIGNAL GENERATION VERIFICATION
-- ================================================================

-- 1.1: Verify all 4 signals are being generated
SELECT
  strategy,
  COUNT(*) as signal_count,
  COUNT(DISTINCT symbol) as unique_symbols,
  MIN(created_at) as oldest_signal,
  MAX(created_at) as newest_signal
FROM trading_signals
WHERE created_at > NOW() - INTERVAL '1 hour'
GROUP BY strategy
ORDER BY strategy;

-- Expected: 4 rows (BOLLINGER_BAND, CONTRARIAN, MACD, MOMENTUM)
-- Each with count > 0


-- ================================================================
-- SECTION 2: MACD SIGNAL VALIDATION
-- ================================================================

-- 2.1: MACD signals by type
SELECT
  signal_type,
  COUNT(*) as count,
  AVG(signal_strength) as avg_strength,
  AVG(confidence) as avg_confidence,
  MIN(signal_strength) as min_strength,
  MAX(signal_strength) as max_strength
FROM trading_signals
WHERE strategy = 'MACD'
  AND created_at > NOW() - INTERVAL '1 hour'
GROUP BY signal_type
ORDER BY signal_type;

-- Expected:
-- STRONG_BUY: strength ~100, confidence ~90
-- STRONG_SELL: strength ~0, confidence ~90
-- BUY: strength ~60-75, confidence ~70-80
-- SELL: strength ~25-40, confidence ~70-80


-- 2.2: MACD indicator values storage
SELECT
  symbol,
  created_at,
  signal_type,
  indicators_used->>'MACD' as macd,
  indicators_used->>'MACD_Signal' as macd_signal,
  indicators_used->>'distance' as distance
FROM trading_signals
WHERE strategy = 'MACD'
  AND created_at > NOW() - INTERVAL '10 minutes'
LIMIT 10;

-- Expected: MACD and MACD_Signal are numeric values, distance is present


-- 2.3: MACD bullish crossovers
SELECT
  symbol,
  created_at,
  signal_strength,
  confidence,
  recommendation
FROM trading_signals
WHERE strategy = 'MACD'
  AND signal_type = 'STRONG_BUY'
  AND created_at > NOW() - INTERVAL '1 hour'
ORDER BY created_at DESC
LIMIT 10;

-- Expected: signal_strength = 100, confidence = 90


-- ================================================================
-- SECTION 3: BOLLINGER BAND SIGNAL VALIDATION
-- ================================================================

-- 3.1: BB signals by type
SELECT
  signal_type,
  COUNT(*) as count,
  AVG(signal_strength) as avg_strength,
  AVG(confidence) as avg_confidence,
  MIN(signal_strength) as min_strength,
  MAX(signal_strength) as max_strength
FROM trading_signals
WHERE strategy = 'BOLLINGER_BAND'
  AND created_at > NOW() - INTERVAL '1 hour'
GROUP BY signal_type
ORDER BY signal_type;

-- Expected:
-- STRONG_BUY: strength = 100, confidence = 85
-- STRONG_SELL: strength = 0, confidence = 85
-- BUY/SELL: strength ~65-70 or ~25-35, confidence = 75


-- 3.2: BB indicator values storage
SELECT
  symbol,
  created_at,
  signal_type,
  indicators_used->>'Price' as price,
  indicators_used->>'BB_Upper' as bb_upper,
  indicators_used->>'BB_Middle' as bb_middle,
  indicators_used->>'BB_Lower' as bb_lower,
  indicators_used->>'Band_Width' as band_width
FROM trading_signals
WHERE strategy = 'BOLLINGER_BAND'
  AND created_at > NOW() - INTERVAL '10 minutes'
LIMIT 10;

-- Expected: All BB values present and numeric


-- 3.3: Verify BB logic consistency (price > upper = STRONG_BUY)
SELECT
  symbol,
  created_at,
  signal_type,
  signal_strength,
  (indicators_used->>'Price')::DECIMAL as price,
  (indicators_used->>'BB_Upper')::DECIMAL as bb_upper,
  CASE
    WHEN (indicators_used->>'Price')::DECIMAL > (indicators_used->>'BB_Upper')::DECIMAL THEN 'PRICE_ABOVE_UPPER'
    WHEN (indicators_used->>'Price')::DECIMAL < (indicators_used->>'BB_Lower')::DECIMAL THEN 'PRICE_BELOW_LOWER'
    ELSE 'PRICE_IN_MIDDLE'
  END as position
FROM trading_signals
WHERE strategy = 'BOLLINGER_BAND'
  AND created_at > NOW() - INTERVAL '10 minutes'
LIMIT 20;

-- Expected: PRICE_ABOVE_UPPER signals should be STRONG_BUY with strength=100


-- ================================================================
-- SECTION 4: CLAUDE AI INTEGRATION VALIDATION
-- ================================================================

-- 4.1: Verify Claude receives all 4 signals
SELECT
  symbol,
  created_at,
  indicators_used->'claude_analysis'->>'action' as action,
  indicators_used->'claude_analysis'->>'confidence' as confidence,
  indicators_used->'claude_analysis'->>'reason' as reason,
  json_object_keys(indicators_used->'claude_analysis'->'all_signals') as signal_types
FROM trading_signals
WHERE indicators_used->'claude_analysis' IS NOT NULL
  AND created_at > NOW() - INTERVAL '1 hour'
LIMIT 5;

-- Expected: action is BUY/SELL/HOLD, confidence 0-100, all_signals contains 4 keys


-- 4.2: Verify all 4 signals present in Claude analysis
SELECT
  symbol,
  created_at,
  indicators_used->'claude_analysis'->'all_signals'->>'momentum' as momentum,
  indicators_used->'claude_analysis'->'all_signals'->>'contrarian' as contrarian,
  indicators_used->'claude_analysis'->'all_signals'->>'macd' as macd,
  indicators_used->'claude_analysis'->'all_signals'->>'bollinger_band' as bollinger_band
FROM trading_signals
WHERE indicators_used->'claude_analysis' IS NOT NULL
  AND created_at > NOW() - INTERVAL '1 hour'
LIMIT 10;

-- Expected: All 4 signals present with JSON structure


-- 4.3: Extract nested signal values from Claude analysis
SELECT
  symbol,
  created_at,
  indicators_used->'claude_analysis'->'all_signals'->'momentum'->>'type' as momentum_type,
  (indicators_used->'claude_analysis'->'all_signals'->'momentum'->>'strength')::INT as momentum_strength,
  indicators_used->'claude_analysis'->'all_signals'->'macd'->>'type' as macd_type,
  (indicators_used->'claude_analysis'->'all_signals'->'macd'->>'strength')::INT as macd_strength,
  (indicators_used->'claude_analysis'->>'confidence')::INT as claude_confidence
FROM trading_signals
WHERE indicators_used->'claude_analysis' IS NOT NULL
  AND created_at > NOW() - INTERVAL '30 minutes'
LIMIT 10;

-- Expected: All values numeric and within valid ranges


-- ================================================================
-- SECTION 5: SIGNAL CONVERGENCE ANALYSIS
-- ================================================================

-- 5.1: Signal agreement matrix (last 10 signals per symbol)
SELECT
  created_at,
  (SELECT signal_type FROM trading_signals s2
   WHERE s2.symbol = s1.symbol
   AND s2.strategy = 'MOMENTUM'
   AND s2.created_at = s1.created_at LIMIT 1) as momentum,
  (SELECT signal_type FROM trading_signals s2
   WHERE s2.symbol = s1.symbol
   AND s2.strategy = 'CONTRARIAN'
   AND s2.created_at = s1.created_at LIMIT 1) as contrarian,
  (SELECT signal_type FROM trading_signals s2
   WHERE s2.symbol = s1.symbol
   AND s2.strategy = 'MACD'
   AND s2.created_at = s1.created_at LIMIT 1) as macd,
  (SELECT signal_type FROM trading_signals s2
   WHERE s2.symbol = s1.symbol
   AND s2.strategy = 'BOLLINGER_BAND'
   AND s2.created_at = s1.created_at LIMIT 1) as bollinger_band
FROM trading_signals s1
WHERE symbol = 'BTCUSDT'
  AND created_at > NOW() - INTERVAL '1 hour'
GROUP BY created_at, s1.symbol
ORDER BY created_at DESC
LIMIT 10;

-- Expected: Clear patterns of convergence/divergence


-- 5.2: Count convergence scenarios
SELECT
  CASE
    WHEN momentum = 'BUY' AND contrarian = 'BUY' AND macd = 'STRONG_BUY' AND bb = 'BUY' THEN 'ALL_BUY'
    WHEN momentum = 'SELL' AND contrarian = 'SELL' AND macd = 'STRONG_SELL' AND bb = 'SELL' THEN 'ALL_SELL'
    WHEN (momentum IN ('BUY', 'STRONG_BUY') AND contrarian IN ('BUY', 'STRONG_BUY')
          AND macd IN ('BUY', 'STRONG_BUY') AND bb IN ('BUY', 'STRONG_BUY')) THEN 'MULTI_BUY'
    WHEN (momentum IN ('SELL', 'STRONG_SELL') AND contrarian IN ('SELL', 'STRONG_SELL')
          AND macd IN ('SELL', 'STRONG_SELL') AND bb IN ('SELL', 'STRONG_SELL')) THEN 'MULTI_SELL'
    ELSE 'MIXED'
  END as convergence_type,
  COUNT(*) as occurrences
FROM (
  SELECT
    created_at,
    (SELECT signal_type FROM trading_signals s2
     WHERE s2.symbol = s1.symbol AND s2.strategy = 'MOMENTUM' AND s2.created_at = s1.created_at LIMIT 1) as momentum,
    (SELECT signal_type FROM trading_signals s2
     WHERE s2.symbol = s1.symbol AND s2.strategy = 'CONTRARIAN' AND s2.created_at = s1.created_at LIMIT 1) as contrarian,
    (SELECT signal_type FROM trading_signals s2
     WHERE s2.symbol = s1.symbol AND s2.strategy = 'MACD' AND s2.created_at = s1.created_at LIMIT 1) as macd,
    (SELECT signal_type FROM trading_signals s2
     WHERE s2.symbol = s1.symbol AND s2.strategy = 'BOLLINGER_BAND' AND s2.created_at = s1.created_at LIMIT 1) as bb
  FROM trading_signals s1
  WHERE symbol = 'BTCUSDT'
    AND created_at > NOW() - INTERVAL '1 hour'
  GROUP BY created_at, s1.symbol
) convergence
GROUP BY convergence_type;

-- Expected: Different convergence types with reasonable distribution


-- ================================================================
-- SECTION 6: SIGNAL STRENGTH DISTRIBUTION
-- ================================================================

-- 6.1: Overall strength distribution
SELECT
  strategy,
  signal_type,
  MIN(signal_strength) as min_strength,
  MAX(signal_strength) as max_strength,
  AVG(signal_strength)::DECIMAL(10,2) as avg_strength,
  STDDEV(signal_strength)::DECIMAL(10,2) as stddev_strength,
  COUNT(*) as count
FROM trading_signals
WHERE created_at > NOW() - INTERVAL '1 hour'
GROUP BY strategy, signal_type
ORDER BY strategy, signal_type;

-- Expected: Reasonable distribution with expected ranges


-- 6.2: Confidence distribution by strategy
SELECT
  strategy,
  MIN(confidence) as min_confidence,
  MAX(confidence) as max_confidence,
  AVG(confidence)::DECIMAL(10,2) as avg_confidence,
  STDDEV(confidence)::DECIMAL(10,2) as stddev_confidence
FROM trading_signals
WHERE created_at > NOW() - INTERVAL '1 hour'
GROUP BY strategy
ORDER BY strategy;

-- Expected: MACD and BB confidence should match specification (70-95 range)


-- ================================================================
-- SECTION 7: PERFORMANCE METRICS
-- ================================================================

-- 7.1: Signal generation rate (signals per minute)
SELECT
  DATE_TRUNC('minute', created_at) as minute,
  COUNT(*) as total_signals,
  COUNT(DISTINCT strategy) as unique_strategies,
  COUNT(DISTINCT symbol) as unique_symbols
FROM trading_signals
WHERE created_at > NOW() - INTERVAL '1 hour'
GROUP BY DATE_TRUNC('minute', created_at)
ORDER BY minute DESC;

-- Expected: Consistent rate with 4 signals per symbol per minute


-- 7.2: Average execution time per symbol
SELECT
  DATE_TRUNC('minute', created_at) as minute,
  COUNT(DISTINCT symbol) as symbols_processed,
  COUNT(*) as signals_generated,
  COUNT(*) / NULLIF(COUNT(DISTINCT symbol), 0) as signals_per_symbol
FROM trading_signals
WHERE created_at > NOW() - INTERVAL '1 hour'
GROUP BY DATE_TRUNC('minute', created_at)
ORDER BY minute DESC;

-- Expected: 4 signals per symbol (always)


-- ================================================================
-- SECTION 8: ERROR & EDGE CASE VALIDATION
-- ================================================================

-- 8.1: Check for NULL indicators
SELECT
  strategy,
  COUNT(CASE WHEN indicators_used IS NULL THEN 1 END) as null_indicators,
  COUNT(CASE WHEN indicators_used::text = '{}' THEN 1 END) as empty_indicators,
  COUNT(*) as total
FROM trading_signals
WHERE created_at > NOW() - INTERVAL '1 hour'
GROUP BY strategy;

-- Expected: No NULL or empty indicators


-- 8.2: Check for Claude analysis presence
SELECT
  COUNT(CASE WHEN indicators_used->'claude_analysis' IS NOT NULL THEN 1 END) as with_claude,
  COUNT(CASE WHEN indicators_used->'claude_analysis' IS NULL THEN 1 END) as without_claude,
  COUNT(*) as total,
  ROUND(100.0 * COUNT(CASE WHEN indicators_used->'claude_analysis' IS NOT NULL THEN 1 END) / COUNT(*), 1) as percent_with_claude
FROM trading_signals
WHERE created_at > NOW() - INTERVAL '1 hour'
  AND strategy IN ('MOMENTUM', 'CONTRARIAN', 'MACD', 'BOLLINGER_BAND');

-- Expected: Most signals should have Claude analysis (unless API is disabled)


-- ================================================================
-- SECTION 9: DATA INTEGRITY CHECKS
-- ================================================================

-- 9.1: Verify signal strength is always 0-100
SELECT
  COUNT(CASE WHEN signal_strength < 0 OR signal_strength > 100 THEN 1 END) as invalid_strength,
  COUNT(*) as total
FROM trading_signals
WHERE created_at > NOW() - INTERVAL '1 hour';

-- Expected: 0 invalid strengths


-- 9.2: Verify confidence is always 0-100
SELECT
  COUNT(CASE WHEN confidence < 0 OR confidence > 100 THEN 1 END) as invalid_confidence,
  COUNT(*) as total
FROM trading_signals
WHERE created_at > NOW() - INTERVAL '1 hour';

-- Expected: 0 invalid confidences


-- 9.3: Verify required fields are populated
SELECT
  COUNT(CASE WHEN signal_type IS NULL THEN 1 END) as null_signal_type,
  COUNT(CASE WHEN signal_strength IS NULL THEN 1 END) as null_strength,
  COUNT(CASE WHEN confidence IS NULL THEN 1 END) as null_confidence,
  COUNT(CASE WHEN recommendation IS NULL OR recommendation = '' THEN 1 END) as null_recommendation,
  COUNT(*) as total
FROM trading_signals
WHERE created_at > NOW() - INTERVAL '1 hour';

-- Expected: All counts should be 0


-- ================================================================
-- SECTION 10: SUMMARY REPORT
-- ================================================================

-- 10.1: Overall P1 status
SELECT
  'Total Signals' as metric,
  COUNT(*)::TEXT as value
FROM trading_signals
WHERE created_at > NOW() - INTERVAL '1 hour'
UNION ALL
SELECT
  'Unique Strategies',
  COUNT(DISTINCT strategy)::TEXT
FROM trading_signals
WHERE created_at > NOW() - INTERVAL '1 hour'
UNION ALL
SELECT
  'Unique Symbols',
  COUNT(DISTINCT symbol)::TEXT
FROM trading_signals
WHERE created_at > NOW() - INTERVAL '1 hour'
UNION ALL
SELECT
  'Signals with Claude Analysis',
  COUNT(*)::TEXT
FROM trading_signals
WHERE indicators_used->'claude_analysis' IS NOT NULL
  AND created_at > NOW() - INTERVAL '1 hour'
UNION ALL
SELECT
  'Average Signal Strength',
  ROUND(AVG(signal_strength), 2)::TEXT
FROM trading_signals
WHERE created_at > NOW() - INTERVAL '1 hour'
UNION ALL
SELECT
  'Average Confidence',
  ROUND(AVG(confidence), 2)::TEXT
FROM trading_signals
WHERE created_at > NOW() - INTERVAL '1 hour';

-- Expected: Balanced metrics showing P1 is working correctly
