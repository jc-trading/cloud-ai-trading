# CloudAiTrading — AI Navigation Index (v3)

> **For AI:** 每次新 session 先读这个文件。旧版描述的 crypto 信号系统已全部删除。
> **Last Updated:** 2026-09-09

---

## 🎯 项目是什么 (Direction v3)

**美股 / ETF 模拟推荐 + 学习平台** — 一个确定性 quant 引擎每天生成买卖建议，
在两个 $2,000 模拟账户里记账（我的练习账户 vs 系统对照账户），Telegram 推送。

- **只做模拟**：没有实盘下单，没有 broker 订单流。
- **没有 crypto**：Binance/CoinGecko/ccxt 数据面已删（migration 013 + R1-8）。
- **确定性引擎**：信号来自 `quant/engine` 纯函数，回测与 live 共用同一份代码，
  不用 LLM 做交易决策。

**技术栈:** FastAPI + PostgreSQL + Redis + Celery + Vue 3 · Docker Compose
**行情源:** Alpaca Data API (bars/snapshots) + Finnhub (real-time quote / search)

---

## 📁 架构地图

### `quant/` — 确定性引擎（framework-free，host venv 跑 R0 回测）
- `config.py` — 只读常量（路径、universe、Master Settings 默认值）
- `data/` — fetch · store (Parquet) · registry (PG file provenance) · corporate_actions · calendar ·
  universe · `bars.get_bars()`（唯一行情入口）
- `engine/` — indicators · signal · strategy · funnel · sizing · exits（全纯函数）
- `backtest/` — costs · simulator · metrics · walkforward · bias_checks
- `research/` — `r09.py` R0-9 walk-forward 校准 + scoreboard
- 数据落在 `cat-data/`（gitignored）：`bars/` Parquet · `r09/` 校准结果

### `backend/app/modules/` — API (FastAPI, `/api/v1`)
**全部已挂载 —— Phase B 之后没有 parked 模块了:**
- `simledger/` — ⭐ 模拟账本：service（记账）· cycles（signal/entry/position 三层
  循环 + 安全状态）· router（/sim/*）
- `market/` — 行情，stocks-only（Alpaca + Finnhub override/search/quote）
- `watchlist/` — 自选列表（market_type 只允许 "stock"）
- `auth/` — JWT + RBAC（角色管理只走 API/psql，前端无 UI）；`/register` 由
  `ALLOW_REGISTER` 开关控制，默认 403
- `system/` — watchdog（心跳/staleness/EOD 告警）· metrics（bar store 体积 gauge）
- `llm/` · `nightwatch/` · `notifications/`（Telegram）· `admin/`

**Phase B 删掉的（2026-09-09）:** `analysis` · `decisions` · `trading` ·
`strategy` · `equity` · `execution` · `fundamentals` · `risk` · `exchange`
模块，配套的 3 个 celery task 模块、前端 ExchangeSettings 页，
以及 migration 017 drop 的 16 张死表。主机监控（psutil/docker 采集、system_logs /
system_metrics / task_status）一并降级删除，liveness 只剩 watchdog + heartbeats。

### `backend/tasks/celery_app.py` — 调度（Beat, UTC）
| Beat 任务 | 时间 | 作用 |
|---|---|---|
| `quant.signal_cycle` | 21:30 UTC 收盘后 | 同步日线 bars → 跑引擎 → 生成明日 recommendations + 每日 exit 管理 |
| `quant.entry_cycle` | 13:36/14:36 UTC 双槽 (09:36 ET, EDT/EST 各一，错的自动 no-op) | 开盘后按建议为系统账户建仓 |
| `quant.position_cycle` | 每 5 min（任务内 gate 到 RTH） | 盘中 stop 检查 |
| `quant.heartbeat` | 每 1 min | 心跳（watchdog 靠它测 staleness） |
| `quant.telegram_poll` | 每 1 min | Telegram 命令轮询（/kill /pause /resume …） |

另有 `market.eod_correction`（01:30 UTC，用 SIP 校正前一交易日 1min bars）。
所有任务带 `expires`，worker 恢复时丢弃积压（07-04 事故规则）。

### `frontend/` — Vue 3 + Vite
views: Recommendations · SimAccount · Market · Watchlist · SymbolDetail ·
Settings · 登录注册。共享页面样式在 `src/assets/main.css`（.jd-* / .d-* chrome）。

---

## 🔒 关键铁律（改代码前必须知道）

1. **引擎纯净**：`quant/engine` 纯函数，无 I/O、无 DB、无时钟 — 回测与 live 共用。
2. **Store RAW, adjust on read**：Parquet 存原始 bars，复权在 `get_bars()` 读取时做。
3. **Simulation only**：任何路径都不许下真实订单；exchange 适配器只读行情。
4. **Stop 必填**：任何建仓（含手动 /sim/trade）必须带 stop，无 stop 拒单。
5. **Fail-closed**：数据缺失/同步失败 → 宁可不交易 + 告警，绝不用陈旧数据下单。
6. **单写者**：simledger 账本只由 cycles/service 写；其他模块只读。
7. **No secrets in git**：API keys 全部走 `.env`。

---

## 🔧 怎么跑

```bash
docker compose up -d                  # postgres · redis · backend · celery worker/beat
docker compose logs -f celery-worker

# 测试：make test 跑 host venv 上的 backend + quant 两套
make test
make test-docker        # 或者跑在容器里

# quant 测试 + 回测（host venv，见 quant/README.md）
source quant/.venv/bin/activate && pytest quant/tests
python -m quant.research.r09 --quick  # 校准 smoke；完整跑 ~1-2h

# 前端
cd frontend && npm run dev            # build: npm run build

# 数据库
docker compose exec postgres psql -U postgres -d cloudaitrading
```

---

## 📚 关键文档

- `qa/R1_code_review_progress.md` — R1 全量 code review 台账（33 confirmed findings + fix batches）
- `qa/R1_browser_qa_report.md` — 浏览器 QA 报告
- `PROFESSIONAL_QUANT_SYSTEM_ASSESSMENT.md` — 专业度评估（OOS 口径、fail-closed gates）
- `cat-data/r09/results.json` — R0-9 walk-forward 校准结果（badge 数据来源）
- `quant/README.md` — quant 包 setup；设计权威见其中 CAT plan 链接
- `docs/` — operations + setup 是当前的；历史文档全部搬进 `docs/archive/`（描述的 crypto 系统已删）

---

## ⚠️ 给未来 session 的提醒

- 旧文档/注释里提到的 Binance、OHLCV 表、4 策略信号、Claude AI 分析 → 全是已删的
  v1 系统，别照着实现。
- Parked modules 已在 Phase B 全删；别从 git 历史里捞回来重新接线。
- 改 money-path（simledger service/cycles/sizing）先读 review 台账里的 invariants。
