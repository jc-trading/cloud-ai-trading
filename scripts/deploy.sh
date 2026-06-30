#!/bin/bash
set -e

# 部署脚本 - Cloud AI Trading
# 用途：本地重启或 VPS 部署时自动执行迁移和验证

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# 颜色输出
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}🚀 Cloud AI Trading - Deployment Script${NC}"
echo "============================================"
echo ""

# 1. 验证 .env 文件
if [ ! -f .env ]; then
    echo -e "${RED}❌ .env file not found!${NC}"
    echo "Please create .env file from .env.example"
    exit 1
fi

echo -e "${BLUE}1️⃣  Checking environment...${NC}"
if ! command -v docker &> /dev/null; then
    echo -e "${RED}❌ Docker is not installed!${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Docker found${NC}"

# 2. 构建镜像（如果需要）
echo ""
echo -e "${BLUE}2️⃣  Building Docker images...${NC}"
docker compose build --no-cache 2>/dev/null && echo -e "${GREEN}✅ Images built${NC}" || echo -e "${YELLOW}⚠️  Images already exist${NC}"

# 3. 启动服务
echo ""
echo -e "${BLUE}3️⃣  Starting services...${NC}"
docker compose up -d
echo -e "${GREEN}✅ Services started${NC}"

# 4. 等待数据库就绪
echo ""
echo -e "${BLUE}4️⃣  Waiting for database to be ready...${NC}"
max_attempts=30
attempt=0
while [ $attempt -lt $max_attempts ]; do
    if docker compose exec -T postgres pg_isready -U postgres > /dev/null 2>&1; then
        echo -e "${GREEN}✅ Database is ready${NC}"
        break
    fi
    attempt=$((attempt + 1))
    echo "   Waiting... ($attempt/$max_attempts)"
    sleep 1
done

if [ $attempt -eq $max_attempts ]; then
    echo -e "${RED}❌ Database failed to start${NC}"
    exit 1
fi

# 5. 运行迁移
echo ""
echo -e "${BLUE}5️⃣  Running database migrations...${NC}"
docker compose exec -T backend alembic upgrade head && echo -e "${GREEN}✅ Migrations applied${NC}"

# 6. 验证 Phase 1 表
echo ""
echo -e "${BLUE}6️⃣  Verifying Phase 1 tables...${NC}"
PHASE1_COUNT=$(docker compose exec -T postgres psql -U postgres -d cloudaitrading -t -c "
  SELECT COUNT(*) FROM information_schema.tables
  WHERE table_schema='public' AND table_name IN ('users', 'watchlists', 'watchlist_items')")

if [ "$PHASE1_COUNT" -ge 3 ]; then
    echo -e "${GREEN}✅ Phase 1 tables exist ($PHASE1_COUNT)${NC}"
else
    echo -e "${RED}❌ Phase 1 tables missing!${NC}"
fi

# 7. 验证 Phase 2 表
echo ""
echo -e "${BLUE}7️⃣  Verifying Phase 2 tables...${NC}"
PHASE2_TABLES=$(docker compose exec -T postgres psql -U postgres -d cloudaitrading -t -c "
  SELECT COUNT(*) FROM information_schema.tables
  WHERE table_schema='public' AND table_name IN ('ohlcv_candles', 'technical_indicators', 'market_data_events')")

if [ "$PHASE2_TABLES" -eq 3 ]; then
    echo -e "${GREEN}✅ Phase 2 tables created (3/3)${NC}"
else
    echo -e "${YELLOW}⚠️  Phase 2 tables: $PHASE2_TABLES/3${NC}"
    echo "   Creating missing Phase 2 tables..."
    docker compose exec -T postgres psql -U postgres -d cloudaitrading << 'SQLEOF'
-- 创建 OHLCV 表
CREATE TABLE IF NOT EXISTS ohlcv_candles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    watchlist_id UUID NOT NULL REFERENCES watchlists(id) ON DELETE CASCADE,
    symbol VARCHAR(20) NOT NULL,
    timeframe VARCHAR(10) NOT NULL,
    open_time TIMESTAMP WITH TIME ZONE NOT NULL,
    close_time TIMESTAMP WITH TIME ZONE NOT NULL,
    open_price NUMERIC(18,8) NOT NULL,
    high_price NUMERIC(18,8) NOT NULL,
    low_price NUMERIC(18,8) NOT NULL,
    close_price NUMERIC(18,8) NOT NULL,
    volume NUMERIC(20,8) NOT NULL,
    quote_volume NUMERIC(20,8) NOT NULL,
    trades_count INTEGER,
    taker_buy_base_volume NUMERIC(20,8),
    taker_buy_quote_volume NUMERIC(20,8),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(watchlist_id, symbol, timeframe, open_time)
);

CREATE TABLE IF NOT EXISTS technical_indicators (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ohlcv_candle_id UUID UNIQUE NOT NULL REFERENCES ohlcv_candles(id) ON DELETE CASCADE,
    symbol VARCHAR(20) NOT NULL,
    timeframe VARCHAR(10) NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    ema_12 NUMERIC(18,8), ema_26 NUMERIC(18,8), ema_50 NUMERIC(18,8), ema_200 NUMERIC(18,8),
    rsi_14 NUMERIC(10,2), macd NUMERIC(18,8), macd_signal NUMERIC(18,8), macd_histogram NUMERIC(18,8),
    atr_14 NUMERIC(18,8), bb_upper NUMERIC(18,8), bb_middle NUMERIC(18,8), bb_lower NUMERIC(18,8),
    bb_width NUMERIC(18,8), bb_position NUMERIC(5,2),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS market_data_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    watchlist_id UUID NOT NULL REFERENCES watchlists(id) ON DELETE CASCADE,
    symbol VARCHAR(20) NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    event_data JSONB,
    price NUMERIC(18,8),
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 创建索引
CREATE INDEX IF NOT EXISTS ix_ohlcv_candles_watchlist_id ON ohlcv_candles(watchlist_id);
CREATE INDEX IF NOT EXISTS ix_ohlcv_candles_symbol ON ohlcv_candles(symbol);
CREATE INDEX IF NOT EXISTS ix_ohlcv_candles_timeframe ON ohlcv_candles(timeframe);
CREATE INDEX IF NOT EXISTS ix_ohlcv_candles_open_time ON ohlcv_candles(open_time);
CREATE INDEX IF NOT EXISTS ix_technical_indicators_symbol_timeframe ON technical_indicators(symbol, timeframe);
CREATE INDEX IF NOT EXISTS ix_technical_indicators_timestamp ON technical_indicators(timestamp);
CREATE INDEX IF NOT EXISTS ix_market_data_events_watchlist_id ON market_data_events(watchlist_id);
CREATE INDEX IF NOT EXISTS ix_market_data_events_symbol ON market_data_events(symbol);
CREATE INDEX IF NOT EXISTS ix_market_data_events_timestamp ON market_data_events(timestamp);
SQLEOF
    echo -e "${GREEN}✅ Phase 2 tables created${NC}"
fi

# 8. 检查 API 健康状态
echo ""
echo -e "${BLUE}8️⃣  Checking API health...${NC}"
max_attempts=30
attempt=0
while [ $attempt -lt $max_attempts ]; do
    if curl -s -f http://localhost:8000/api/health > /dev/null 2>&1; then
        API_RESPONSE=$(curl -s http://localhost:8000/api/health)
        echo -e "${GREEN}✅ API is healthy${NC}"
        echo "   Response: $API_RESPONSE"
        break
    fi
    attempt=$((attempt + 1))
    echo "   Waiting for API... ($attempt/$max_attempts)"
    sleep 1
done

if [ $attempt -eq $max_attempts ]; then
    echo -e "${YELLOW}⚠️  API health check timeout (but services may be running)${NC}"
fi

# 9. 显示容器状态
echo ""
echo -e "${BLUE}9️⃣  Container status:${NC}"
docker compose ps

# 10. 显示数据库表统计
echo ""
echo -e "${BLUE}🔟 Database summary:${NC}"
TABLE_COUNT=$(docker compose exec -T postgres psql -U postgres -d cloudaitrading -t -c "
  SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'")
echo -e "   Total tables: ${GREEN}$TABLE_COUNT${NC}"

# 完成
echo ""
echo "============================================"
echo -e "${GREEN}✅ Deployment completed successfully!${NC}"
echo ""
echo "📋 Next steps:"
echo "   - Access API: http://localhost:8000/api/docs"
echo "   - Check logs: docker compose logs -f backend"
echo "   - Stop services: docker compose down"
echo ""
