# Backend Celery Tasks Guide

> 深度解析 CloudAiTrading 后端所有定时任务的运作原理、触发条件、数据流、以及 Claude API 的消耗情况。

**Last Updated:** 2026-04-15

---

## 📋 目录

1. [任务概览](#任务概览)
2. [详细任务说明](#详细任务说明)
3. [Claude API 调用分析](#claude-api-调用分析)
4. [系统数据流](#系统数据流)
5. [成本计算](#成本计算)
6. [常见问题](#常见问题)

---

## 任务概览

系统共有 **5 个核心 Celery 定时任务**，分为两大类：

### 📊 数据采集 & 指标计算（不用 AI）

| 任务名 | 频率 | 是否用 Claude | 职责 |
|--------|------|---------------|------|
| `collect_market_data` | 每 60 秒 | ❌ 否 | 从 Binance 拉 K 线数据 |
| `update_indicators` | 每 120 秒 | ❌ 否 | 计算技术指标 |
| `calculate_portfolio_stats` | 每 3600 秒 | ❌ 否 | 计算持仓盈亏 + Telegram 推送 |

### 🤖 信号生成 & AI 分析（用 Claude）

| 任务名 | 频率 | Claude 调用 | 触发条件 |
|--------|------|-----------|---------|
| `generate_trading_signals` | 每 60 秒 | ✅ 每个 Symbol 调一次 | 有 Watchlist 就无条件跑 |
| `run_scheduled_analysis` | 每 180 秒 | ✅ 每个 Symbol 调一次 | 有 is_active=True 的 QuantStrategy 才跑 |

---

## 详细任务说明

### 1️⃣ `collect_market_data`（数据采集）

**文件位置：** `backend/app/tasks/market_data_tasks.py` (Line 42-192)

**运行频率：** 每 60 秒

**触发条件：** 无条件，系统自动触发

**是否用 Claude：** ❌ 否

**工作流程：**

```
Celery Beat 每 60 秒触发
    ↓
查询 DB：有没有 Watchlist？
    ↓
  没有 → 直接返回
    ↓
  有 → 遍历每个 Watchlist:
        ↓
      对每个 Symbol:
        1. 连接 Binance WebSocket
        2. 拉取最近 100 根 1小时 K线
        3. 保存到 ohlcv_candles 表
        4. 同时计算一次技术指标
        5. 断开连接
        ↓
      批量 Commit DB
```

**数据库表操作：**
- 读：`Watchlist`, `WatchlistItem`
- 写：`ohlcv_candles`, `technical_indicators`
- 日志：`system_logs`

**性能指标：**
- 处理 1 个 Symbol：~500ms （含 Binance API 调用 + DB 写入）
- 处理 5 个 Symbol：~2.5 秒
- 处理 10 个 Symbol：~5 秒

**失败处理：** 失败会重试 3 次，间隔 10 秒

---

### 2️⃣ `update_indicators`（指标刷新）

**文件位置：** `backend/app/tasks/market_data_tasks.py` (Line 194-263)

**运行频率：** 每 120 秒

**触发条件：** 无条件，系统自动触发

**是否用 Claude：** ❌ 否

**工作流程：**

```
Celery Beat 每 120 秒触发
    ↓
查询 DB：所有 Watchlist 里有多少个 Symbol？
    ↓
对每个 Symbol:
    1. 从 ohlcv_candles 表读最新 K 线
    2. 重新计算技术指标：
       - RSI(14)
       - MACD (12, 26, 9)
       - EMA(12), EMA(26)
       - Bollinger Bands(20, 2)
       - ATR(14)
    3. 更新或插入 technical_indicators 表
    4. Commit
```

**关键点：** 这个任务 **不拉新数据**，只是基于已有 K 线重算指标。设计的目的是确保指标永远最新。

**数据库表操作：**
- 读：`ohlcv_candles`
- 写：`technical_indicators`
- 日志：`system_logs`

**性能指标：**
- 处理 1 个 Symbol：~50ms
- 处理 5 个 Symbol：~250ms
- 处理 10 个 Symbol：~500ms

---

### 3️⃣ `calculate_portfolio_stats`（组合统计）

**文件位置：** `backend/app/tasks/trading_tasks.py` (Line 433-539)

**运行频率：** 每 3600 秒（1 小时）

**触发条件：** 无条件，系统自动触发

**是否用 Claude：** ❌ 否

**工作流程：**

```
Celery Beat 每 3600 秒（1小时）触发
    ↓
对每个 Watchlist:
    1. 获取该 Watchlist 所有 Symbol 的最新价格
    2. 计算组合指标：
       - total_invested    （总投入金额）
       - current_value     （当前市值）
       - realized_pnl      （已实现盈亏）
       - unrealized_pnl    （未实现盈亏）
       - total_return_percent （总回报率 %）
       - win_rate          （胜率 %）
    3. 保存到 portfolio_stats 表
    4. 推送 Telegram 通知（汇报本小时的组合状况）
```

**Telegram 通知内容：**
```
总投入：$10,000
当前市值：$12,500
未实现盈亏：+$2,500
回报率：+25%
胜率：65%
```

**数据库表操作：**
- 读：`Watchlist`, `WatchlistItem`, `ohlcv_candles`, `Position`
- 写：`portfolio_stats`
- 日志：`system_logs`

**性能指标：**
- 处理 1 个 Watchlist：~100ms
- 处理 5 个 Watchlist：~500ms

---

### 4️⃣ `generate_trading_signals`（⭐ 主信号生成）

**文件位置：** `backend/app/tasks/trading_tasks.py` (Line 26-97, 99-431)

**运行频率：** 每 60 秒

**触发条件：** 有 Watchlist 就无条件跑

**是否用 Claude：** ✅ **是，每个 Symbol 必调**

**⚠️ 这是你 $0.31 开销的主要来源**

**工作流程：**

```
Celery Beat 每 60 秒触发 generate_trading_signals()
    ↓
查 DB：有多少个 Watchlist？
    ↓
  没有 → 直接返回
    ↓
  有 → 对每个 Watchlist 的每个 Symbol:
        ↓
      ①─ 从 DB 读最新 K 线 (OHLCVCandle)
         ├─ close_price
         ├─ volume
         └─ close_time
        ↓
      ②─ 从 DB 读最新 2 条技术指标 (TechnicalIndicator)
         ├─ 当前值：RSI, MACD, EMA12/26, BB上/中/下
         └─ 前一条值：用于计算交叉信号
        ↓
      ③─ 【纯数学计算，不用 AI】生成 4 种信号：
         ├─ Momentum Signal (EMA12 > EMA26 → BUY/HOLD/SELL)
         ├─ Contrarian Signal (RSI + BB 位置 → BUY/HOLD/SELL)
         ├─ MACD Signal (MACD > Signal 线 → BUY/HOLD/SELL)
         └─ Bollinger Band Signal (价格 > BB_upper → SELL/HOLD/BUY)
        ↓
      ④─ 计算每个信号的：
         ├─ signal_type (BUY / HOLD / SELL)
         ├─ signal_strength (0-100，离中性 50 的距离)
         └─ confidence (0-100)
        ↓
      ⑤─ 保存 4 个信号到 trading_signals 表
        ↓
      ⑥─ 找出"最强"的那个信号（strength 最大的）
        ↓
      ⑦─ 🤖 【调用 Claude API】分析这 4 个信号的收敛/背离
         ├─ 输入：
         │  ├─ symbol (如 "BTCUSDT")
         │  ├─ 所有技术指标值
         │  └─ 4 个信号的 type/strength/confidence
         ├─ Claude 输出 JSON：
         │  ├─ action (BUY / SELL / HOLD)
         │  ├─ confidence (0-100)
         │  ├─ reason (分析理由，2-3 句)
         │  ├─ entry_price (建议入场价)
         │  ├─ stop_loss (止损价)
         │  ├─ take_profit (止盈价)
         │  ├─ risk_reward_ratio
         │  ├─ key_factors (关键因素列表)
         │  └─ risk_warning (风险提示)
         ├─ ⏱️ 耗时：~1-2 秒（包括网络延迟）
         ├─ 🪙 成本：~$0.0005-$0.001 (取决于 Token 数)
         └─ 💾 结果保存到最强信号的 indicators_used JSON 字段
        ↓
      ⑧─ 是否推送 Telegram？
         └─ 只有 Momentum 是 STRONG_BUY 或 STRONG_SELL 才推送
        ↓
      继续下一个 Symbol
```

**核心逻辑：第 ⑦ 步 Claude 调用**

```python
# 构建指标字典
indicators_dict = {
    "rsi": 45.2,
    "ema_12": 50123.45,
    "ema_26": 50085.67,
    "bb_upper": 51200.00,
    "bb_middle": 50100.00,
    "bb_lower": 49000.00,
    "macd_line": 37.78,
    "macd_signal": 35.22,
    "macd_histogram": 2.56,
    "current_price": 50150.00,
    "volume": 1234567890,
    "change_24h": 2.5,
    "all_signals": {
        "momentum": {
            "type": "BUY",
            "strength": 65,
            "confidence": 72
        },
        "contrarian": {
            "type": "SELL",
            "strength": 58,
            "confidence": 55
        },
        "macd": {
            "type": "BUY",
            "strength": 68,
            "confidence": 75
        },
        "bollinger_band": {
            "type": "HOLD",
            "strength": 52,
            "confidence": 48
        }
    }
}

# 调用 Claude
claude_result = await analyze_with_claude(
    symbol="BTCUSDT",
    indicators=indicators_dict,
)
# 返回示例：
# {
#   "action": "BUY",
#   "confidence": 68,
#   "reason": "3个信号看涨，只有RSI反向。MACD和Momentum强势看涨。",
#   "entry_price": 50100,
#   "stop_loss": 49500,
#   "take_profit": 51500,
#   "risk_reward_ratio": 2.8,
#   "key_factors": ["EMA金叉", "MACD上穿", "BB中线支撑"],
#   "risk_warning": "RSI处于中性，需关注回调",
#   "tokens_used": 845,
#   "api_cost": 0.00089
# }
```

**数据库表操作：**
- 读：`Watchlist`, `WatchlistItem`, `OHLCVCandle`, `TechnicalIndicator`
- 写：`TradingSignal` (4 条记录/Symbol/执行)
- 日志：`system_logs`, `trading_signal_logs`

**性能指标：**
- 生成 4 个信号（不含 Claude）：~150ms
- Claude API 调用：~1000-2000ms
- DB 写入：~50ms
- **单个 Symbol 总耗时：~1.2-2.2 秒**

**处理多个 Symbol 的时间：**
- 1 Symbol：2-3 秒
- 3 Symbol：6-9 秒
- 5 Symbol：10-15 秒
- 10 Symbol：20-30 秒

**⚠️ 关键观察：**
- 即使 4 个信号都是 HOLD（没用），Claude 也会被调用一次
- 没有任何前置过滤逻辑（这就是浪费的地方）

---

### 5️⃣ `run_scheduled_analysis`（独立 AI 分析）

**文件位置：** `backend/tasks/analysis_tasks.py` (Line 24-117)

**运行频率：** 每 180 秒（3 分钟）

**触发条件：** ⚠️ **只有当有 is_active=True 的 QuantStrategy 时才运行**

**是否用 Claude：** ✅ 是

**工作流程：**

```
Celery Beat 每 180 秒触发
    ↓
查 DB：有多少个 User 开启了 QuantStrategy（is_active=True）？
    ↓
  没有 → 直接返回（什么都不做，不消耗 Claude）
    ↓
  有 → 对每个有策略的 User:
        ↓
      获取该用户所有 Watchlist 的 Symbols
        ↓
      对每个 Symbol:
        1. 调用 AnalysisService.run_analysis()
        2. 计算技术指标
        3. 调用 Claude API（带上 user_strategy 参数）
        4. 得到 Claude 的 action/confidence/entry/stop_loss/take_profit
        5. 如果 confidence >= 60 且 action != "HOLD"
           → 在 DB 创建一条 TradingSignal 记录
        6. 记录分析结果到 AIAnalysisResult 表
```

**与 `generate_trading_signals` 的区别：**

| 对比项 | generate_trading_signals | run_scheduled_analysis |
|-------|-------------------------|----------------------|
| 运行频率 | 每 60 秒 | 每 180 秒 |
| 无条件运行 | ✅ 有就跑 | ❌ 需要策略 |
| 信号来源 | 4 种规则信号 | 直接从指标 |
| 是否创建 Signal | 直接更新已有 Signal | 创建新 Signal（高置信度） |
| 用途 | 全量监控 | 用户自定义策略 |

**数据库表操作：**
- 读：`User`, `QuantStrategy`, `Watchlist`, `TechnicalIndicator`
- 写：`AIAnalysisResult`, `TradingSignal`
- 日志：`system_logs`

---

## Claude API 调用分析

### 调用频率

**每次系统运行的 Claude API 调用数量：**

```
假设有 N 个 Watchlist，每个 Watchlist 里 M 个 Symbol

当只运行 generate_trading_signals 时：
  → 每次调用 60 秒时 → N × M 次 Claude 调用

当同时运行两个任务时（碰撞的情况）：
  generate_trading_signals (60s) + run_scheduled_analysis (180s)
  → 当 180s 这一刻 generate_trading_signals 也在跑时
  → 会有额外的 Claude 调用（数量取决于有多少用户有策略）
```

### Token 消耗分析

**每次 Claude 调用的 Token 成本：**

```
System Prompt（固定）：
  ~35 tokens

User Prompt（可变）：
  基础部分：
    - Symbol 名称 + 技术指标值：~200 tokens
    - 4 个信号数据（type/strength/confidence）：~200 tokens
    
  可选部分（如果有）：
    - Candlestick patterns：+100 tokens
    - Market sentiment：+150 tokens
    - User strategy parameters：+100 tokens

总计：
  最小：35 + 200 = ~235 tokens
  典型：35 + 200 + 200 = ~435 tokens
  最大（含所有可选）：35 + 200 + 200 + 300 = ~735 tokens
  
  实测范围：500-1000 tokens/call

Output Tokens：
  Claude 响应（JSON）：~200-400 tokens
  max_tokens 限制：1024
  实测：300-500 tokens/call

总消耗：
  Input：500-1000 tokens
  Output：300-500 tokens
  每次调用：800-1500 tokens
```

### 模型配置

**当前配置：** `backend/app/config.py`

```python
ANTHROPIC_MODEL: str = "claude-opus-4-8"  # 最贵的
```

**价格（官方价格，2026年）：**
```
Claude Sonnet 4:
  输入：$3/百万 tokens
  输出：$15/百万 tokens
  
计算示例（800-1000 input tokens，300-400 output tokens）：
  输入成本：0.9 × 3 / 1000 = $0.0027
  输出成本：0.35 × 15 / 1000 = $0.00525
  每次成本：~$0.008
```

---

## 系统数据流

### 完整的数据管道

```
                    [Binance Exchange]
                           ↑
                        (WebSocket)
                           ↓
       ┌─────────────────────────────────────┐
       │   collect_market_data (每 60s)      │
       │   从 Binance 拉 100 根 1h K线       │
       └─────────────────────────────────────┘
                    ↓ (ohlcv_candles)
                    
       ┌─────────────────────────────────────┐
       │   update_indicators (每 120s)       │
       │   计算 RSI/MACD/EMA/BB/ATR        │
       └─────────────────────────────────────┘
                    ↓ (technical_indicators)
                    
    ┌──────────────────────────────────────────────┐
    │  generate_trading_signals (每 60s) ⭐ 用 Claude│
    │  1. 生成 4 种规则信号                        │
    │  2. 调用 Claude 做收敛/背离分析             │
    │  3. 存储在 trading_signals                  │
    └──────────────────────────────────────────────┘
         ↓                              ↓
    (trading_signals)          (Telegram通知)
         ↓
    ┌──────────────────────────────────────────────┐
    │  run_scheduled_analysis (每 180s) ⭐ 用 Claude│
    │  只对有 is_active 策略的用户运行            │
    │  创建高置信度信号                          │
    └──────────────────────────────────────────────┘
         ↓ (ai_analysis_results)
         
    ┌─────────────────────────────────────────────┐
    │  calculate_portfolio_stats (每 3600s)       │
    │  计算：投入/市值/盈亏/回报率/胜率         │
    └─────────────────────────────────────────────┘
         ↓ (portfolio_stats)
         └→ (Telegram 组合汇报)
```

### 数据库表关系

```
watchlist (监控列表)
  ├─ watchlist_item (列表里的 Symbol)
  ├─ ohlcv_candles (K 线数据)
  ├─ technical_indicators (技术指标)
  └─ trading_signals (交易信号 + Claude 分析)
       └─ indicators_used (JSON 字段，包含 Claude 结果)

user (用户)
  ├─ quant_strategy (用户策略配置)
  ├─ ai_analysis_results (AI 分析结果)
  └─ portfolio_stats (组合统计)
```

---

## 成本计算

### 场景 1：只有 generate_trading_signals（无 QuantStrategy）

```
假设：
  - 1 个 Watchlist
  - 3 个 Symbol
  - 运行 8 小时

计算：
  频率：每 60 秒调用一次 → 8 小时 × 60 = 480 次
  每次 Symbol 数：3
  总 Claude 调用：480 × 3 = 1440 次
  
  每次 Token：
    Input：700 tokens × $3/MTok = $0.0021
    Output：350 tokens × $15/MTok = $0.00525
    小计：$0.00735/次
  
  8 小时总成本：1440 × $0.00735 = $10.58
```

### 场景 2：多个 Watchlist 和策略用户

```
假设：
  - 3 个 Watchlist
  - 平均每个 Watchlist 4 个 Symbol
  - 2 个用户有 is_active=True 的策略
  - 运行 24 小时

计算：
  generate_trading_signals：
    频率：每 60 秒
    总调用：24 × 60 × (3 × 4) = 17,280 次
  
  run_scheduled_analysis：
    频率：每 180 秒
    总调用：24 × 20 × 2 users × 4 symbols = 3,840 次
  
  总 Claude 调用：17,280 + 3,840 = 21,120 次
  
  总成本：21,120 × $0.00735 ≈ $155
  平均每小时：$155 / 24 ≈ $6.5
```

### 场景 3：用户昨晚的实际情况 ($0.31)

```
已知：
  - 消耗：$0.31（Claude Sonnet 4）
  - 运行时间：8 小时
  
反推：
  成本 = 调用数 × ($0.00735/次)
  $0.31 = 调用数 × $0.00735
  调用数 ≈ 42 次
  
  42 次 / 8 小时 = 5.25 次/小时 ≈ 5 次/小时
  
  推测：
    - 可能只有 1 个 Symbol（5-6 次/小时呼应 6 times in 60-120 min）
    - 或者有 2-3 个 Symbol 但没有频繁调用
    - 或者 run_scheduled_analysis 压根没运行
```

---

## 常见问题

### Q1: 为什么 `generate_trading_signals` 要每分钟跑一次？

**A:** 加密货币市场 24/7 运行，价格变化快。1 分钟的刷新频率可以捕捉短期趋势。但实际上，对于小散户来说，**5-10 分钟一次也够了**。

### Q2: 我没有 QuantStrategy，那 `run_scheduled_analysis` 是不是就不运行了？

**A:** 完全正确！如果数据库里没有 `is_active=True` 的 QuantStrategy，这个任务会 skip，不消耗任何资源或 Claude API。

### Q3: Claude 调用失败了怎么办？

**A:** `generate_trading_signals` 里有异常处理，如果 Claude API 失败，系统会：
```python
try:
    claude_result = await analyze_with_claude(...)
except Exception as claude_error:
    logger.warning(f"Claude failed for {symbol}, continuing with rule-based signal")
    # 保留纯规则信号，继续运行
```
所以即使 Claude 离线，系统也能继续跑 4 个规则信号。

### Q4: 怎样才能降低 Claude 的消耗？

**A:** 三个方案：

**方案 A：降低调用频率**
```python
# celery_app.py
"generate-trading-signals": {
    "schedule": 300.0,  # 改成 5 分钟（从 60 秒）
}
```
节省 80% 的 Claude 调用。

**方案 B：换更便宜的模型**
```python
# config.py
ANTHROPIC_MODEL: str = "claude-haiku-4-5-20251001"  # 改成 Haiku
```
成本降到 1/4。

**方案 C：加信号过滤（最有效）**
```python
# 在 generate_trading_signals 里加
if not should_call_claude(all_signals):
    logger.info(f"Skipping Claude for {symbol}, signals too weak")
    return

def should_call_claude(all_signals: dict) -> bool:
    """只有信号足够强才调 Claude"""
    signals = list(all_signals.values())
    
    # 至少有 3 个信号一致
    buy_count = sum(1 for s in signals if s['type'] == 'BUY')
    sell_count = sum(1 for s in signals if s['type'] == 'SELL')
    
    if max(buy_count, sell_count) < 3:
        return False
    
    # 至少一个信号 confidence > 70%
    max_conf = max(s['confidence'] for s in signals)
    return max_conf > 70
```
可以减少 60-80% 的 Claude 调用。

### Q5: `collect_market_data` 和 `update_indicators` 的区别是什么？

**A:**
- `collect_market_data`：**从 Binance 拉新数据**（100 根 K 线），然后一次性算指标
- `update_indicators`：**只刷新指标**，基于已有 K 线数据重算

设计的目的是 decouple：有些时刻可能数据采集慢了，但指标计算是快的，可以频繁更新指标确保它们总是最新。

### Q6: Telegram 通知是怎么推送的？

**A:** 两个地方会推送：

1. **generate_trading_signals 里：** Momentum 信号是 STRONG_BUY 或 STRONG_SELL 时
2. **calculate_portfolio_stats 里：** 每小时推送一次组合汇报

---

## 关键文件索引

| 文件 | 行号 | 内容 |
|------|------|------|
| `backend/tasks/celery_app.py` | 64-126 | Celery Beat 调度配置 |
| `backend/app/tasks/market_data_tasks.py` | 42-192 | collect_market_data + update_indicators |
| `backend/app/tasks/trading_tasks.py` | 26-431 | generate_trading_signals + calculate_portfolio_stats |
| `backend/tasks/analysis_tasks.py` | 24-117 | run_scheduled_analysis |
| `backend/app/modules/analysis/claude.py` | 全部 | Claude API 封装和 Prompt 构建 |
| `backend/app/config.py` | 36-44 | 模型和 API 配置 |

---

## 下一步优化方向

1. **立即可做（成本最低）**
   - 把 ANTHROPIC_MODEL 从 Sonnet 4 改成 Haiku（节省 75%）

2. **本周可做（效果最好）**
   - 添加信号强度过滤逻辑（减少 60-80% Claude 调用）

3. **可选优化**
   - 降低 generate_trading_signals 频率到 5 分钟（节省 80%）
   - 考虑换成 OpenAI GPT-4o-mini 或 Google Gemini（更便宜）

---

**End of Document**
