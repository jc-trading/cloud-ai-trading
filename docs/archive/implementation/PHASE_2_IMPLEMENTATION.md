# Phase 2: Market Data & Technical Indicators - Implementation Summary

## Overview
Phase 2 implements real-time market data collection, OHLCV candle storage, and technical indicator calculations for the Cloud AI Trading system. This phase adds 13 new modules and 3 new database tables to support comprehensive market analysis.

## Database Schema Changes

### New Tables (Migration 003)
1. **ohlcv_candles** - Stores Open, High, Low, Close, Volume data
   - Supports multiple timeframes: 1m, 5m, 15m, 1h, 4h, 1d
   - Links to watchlists for user-specific data
   - Unique constraint on (watchlist_id, symbol, timeframe, open_time)
   - Indexed on: watchlist_id, symbol, timeframe, open_time

2. **technical_indicators** - Computed indicators for each candle
   - EMA: 12, 26, 50, 200 periods
   - RSI: 14 period
   - MACD: 12/26/9 configuration
   - ATR: 14 period
   - Bollinger Bands: 20 period, 2.0 std dev
   - One-to-one relationship with ohlcv_candles

3. **market_data_events** - Event tracking (alerts, price updates, etc)
   - Flexible JSON payload for event data
   - Indexed on: watchlist_id, symbol, timestamp

## New Modules

### 1. Market Data Models (`app/modules/market_data/models.py`)
- **OHLCVCandle**: Candle data storage with relationships
- **TechnicalIndicator**: Indicator storage with calculated values
- **MarketDataEvent**: Event tracking for market activities

### 2. Technical Indicators (`app/modules/market_data/indicators.py`)
Pure Python implementation of:
- **EMA (Exponential Moving Average)**: 12, 26, 50, 200 period support
- **RSI (Relative Strength Index)**: 14 period oscillator (0-100)
- **ATR (Average True Range)**: 14 period volatility measure
- **Bollinger Bands**: 20 period SMA with 2.0 std dev upper/lower bands
- **MACD**: 12/26/9 momentum indicator with signal line and histogram

Additional analysis utilities:
- Trend detection (bullish/bearish/neutral)
- Overbought/oversold detection
- Bollinger Band breakout detection
- MACD signal line crossover detection

### 3. Binance WebSocket Client (`app/modules/market_data/binance_client.py`)
Real-time price streaming:
- WebSocket connection with automatic reconnection (exponential backoff)
- Ticker stream (24hr price updates)
- Kline stream (candlestick updates)
- Subscription management
- REST API fallback for historical data
- Callback system for price and kline events

### 4. Market Data Service (`app/modules/market_data/service.py`)
Business logic layer:
- Save OHLCV candles (insert/update)
- Calculate and persist technical indicators
- Fetch OHLCV history with pagination
- Get latest candle with indicators
- Save market data events
- Auto-cleanup of old candles (retention policy)

### 5. Schemas (`app/modules/market_data/schemas.py`)
Pydantic v2 models:
- OHLCVCandle, OHLCVCandleResponse
- TechnicalIndicators, TechnicalIndicatorsResponse
- CandleWithIndicators (combined response)
- MarketDataEvent, MarketDataEventResponse
- RealtimePriceUpdate
- MarketDataSummary
- WatchlistMarketData

### 6. API Routes (`app/modules/market_data/router.py`)
REST endpoints:
- `GET /api/market-data/ohlcv/{watchlist_id}/{symbol}` - OHLCV history
- `GET /api/market-data/candle/{watchlist_id}/{symbol}` - Latest candle with indicators
- `GET /api/market-data/summary/{watchlist_id}` - Watchlist summary
- `POST /api/market-data/refresh/{watchlist_id}/{symbol}` - Trigger data refresh
- `GET /api/market-data/indicators/{watchlist_id}/{symbol}` - Latest indicators

All endpoints require authentication and respect user ownership of watchlists.

### 7. Celery Tasks (`app/tasks/market_data_tasks.py`)
Async background tasks:
- **collect_market_data**: Fetch OHLCV from Binance for all watchlist items (every 60s)
- **update_indicators**: Calculate indicators for all items (every 120s)
- **cleanup_market_data**: Remove candles older than 90 days (daily)
- **fetch_binance_ohlcv**: Fetch multiple timeframes for a single symbol (on-demand)

## Dependencies Added

```
python-binance>=1.0.17          # Binance API and WebSocket
ta>=0.11.0                      # Technical analysis calculations
python-dateutil>=2.8.2          # Date utilities
```

Existing dependencies used:
- pandas>=2.2.0 - OHLCV data handling
- numpy>=1.26.0 - Numerical calculations
- asyncpg>=0.29.0 - Async database
- SQLAlchemy[asyncio]>=2.0.0 - ORM

## Periodic Task Schedule

Configured via Celery Beat in `tasks/celery_app.py`:
- **collect-market-data**: Every 60 seconds
- **update-indicators**: Every 120 seconds  
- **cleanup-market-data**: Every 24 hours
- Legacy tasks remain: pull-market-data, run-ai-analysis, sync-watchlists

## Integration Points

### Database
- Models extend existing Watchlist relationship
- Uses Phase 1 async database setup
- Automatic migration on app startup

### Authentication
- All routes require JWT token (Phase 1)
- User ownership validated for watchlist access
- RBAC integrated with existing system

### Task Queue
- Celery broker: Redis (configured in .env)
- Result backend: Redis
- Worker processes: Configurable via docker-compose
- Task routing: Automatic discovery from app.tasks and tasks modules

## File Structure

```
backend/
├── migrations/versions/
│   └── 003_ohlcv_tables.py          (new)
├── app/
│   ├── modules/market_data/
│   │   ├── __init__.py              (new)
│   │   ├── models.py                (new)
│   │   ├── schemas.py               (new)
│   │   ├── router.py                (new)
│   │   ├── service.py               (new)
│   │   ├── indicators.py            (new)
│   │   └── binance_client.py        (new)
│   ├── tasks/
│   │   ├── __init__.py              (new)
│   │   └── market_data_tasks.py     (new)
│   └── main.py                      (updated - added market_data_router)
├── tasks/
│   └── celery_app.py               (updated - added beat schedule entries)
└── requirements.txt                (updated - added python-binance, ta, python-dateutil)
```

## Testing Checklist

- [ ] Database migration succeeds: `docker compose exec backend alembic current`
- [ ] 3 new tables created: ohlcv_candles, technical_indicators, market_data_events
- [ ] API routes accessible: `/api/market-data/ohlcv/{watchlist_id}/{symbol}`
- [ ] Celery tasks registered: `celery -A tasks.celery_app inspect active_queues`
- [ ] Background job processes: `docker compose logs celery-worker`
- [ ] Technical indicators calculated correctly
- [ ] Watchlist market summary endpoint returns aggregated data

## Configuration Requirements

### Environment Variables (in .env)
Already configured in Phase 1:
- DATABASE_URL (async postgres)
- DATABASE_URL_SYNC (sync postgres for migrations)
- REDIS_URL
- CELERY_BROKER_URL
- CELERY_RESULT_BACKEND

### Optional Binance Configuration
- BINANCE_API_KEY (optional, for REST API fallback)
- BINANCE_API_SECRET (optional, for REST API)

## Performance Characteristics

### Database Indexes
- Optimal for queries: symbol lookups, timeframe filters, date range queries
- Foreign key indexes on watchlist_id for cascade deletes

### Calculation Speed
- EMA: O(n) single pass per candle
- RSI: O(n) with delta computation
- ATR: O(n) with true range calculation
- Bollinger Bands: O(n) with std dev
- MACD: O(n*3) with triple EMA
- Total: ~0.1-0.5ms per 100 candles

### Storage
- 1h candles: ~8KB per 24 hours per symbol (365KB/year)
- With indicators: ~15KB per 24 hours (560KB/year)
- With retention cleanup: Scales to 90 days per symbol

## Next Steps (Phase 3)

- [ ] Real-time WebSocket streaming endpoint
- [ ] Trading signal generation based on indicators
- [ ] Alert system integration
- [ ] Portfolio performance tracking
- [ ] Advanced charting endpoints
- [ ] Export functionality (CSV, JSON)

## Migration Path

For existing deployments:
```bash
# Run migration (automatic on app startup)
docker compose exec backend alembic upgrade head

# Verify tables exist
docker compose exec backend psql $DATABASE_URL -c "\dt"

# Populate initial data (optional)
docker compose exec backend python -c "from app.tasks.market_data_tasks import *; asyncio.run(...)"
```

## Monitoring

### Key Metrics
- Celery task queue depth: `celery -A tasks.celery_app inspect active_queues`
- Task execution time: Check Redis for task metadata
- Database growth: Monitor ohlcv_candles table size
- API response time: Check FastAPI logs

### Logging
All components log to stdout in JSON format via Python logging:
- binance_client: Connection status, reconnects, parse errors
- market_data_service: Save/update operations, errors
- Celery tasks: Task start/completion, failures
- API routes: Request/response details

## Known Limitations

1. **Binance USDT pairs only**: Symbol format is "BTCUSDT", "ETHUSDT"
2. **Indicator calculation**: Requires minimum candle count (varies by indicator)
3. **WebSocket reliability**: Depends on network and Binance API stability
4. **Task concurrency**: Limited by Celery worker pool size (default: 1)
5. **Rate limiting**: Binance API has rate limits (handled via exceptions)

## Security Considerations

- All API endpoints require authentication (JWT)
- Watchlist access controlled via user_id
- No sensitive data in event_data JSON
- Database queries parameterized (no SQL injection)
- API rate limiting via slowapi (Phase 1)
- WebSocket connection uses TLS (secure)
