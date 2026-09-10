# ☁️ Cloud AI Trading (CAT)

**美股 / ETF 模拟推荐 + 学习平台。** 一个确定性 quant 引擎每天收盘后生成买卖建议，
在模拟账本里记账（练习账户 vs 系统对照账户），用 Telegram 推送。

**没有实盘下单**，也没有 crypto —— Binance/ccxt 数据面已在 R1-8 删除。
架构地图和改代码前必须知道的铁律见 [`CLAUDE.md`](CLAUDE.md)。

---

## 🚀 怎么跑

前提：Docker Desktop 已就绪，`.env` 已按 `.env.example` 填好
（`SECRET_KEY` · `ENCRYPTION_KEY` · `ANTHROPIC_API_KEY` · `REDIS_PASSWORD` 是必填）。

> **已有安装升级注意**：redis 现在要密码。下一条 `docker compose` 命令之前，先往
> `.env` 加 `REDIS_PASSWORD=<openssl rand -hex 24>`，并把 host 的 `REDIS_URL` /
> `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` 改成
> `redis://:<密码>@localhost:6380/{0,1,2}` —— 否则 compose 会直接报缺变量退出。
> 详见 [docs/setup/Deployment.md](docs/setup/Deployment.md)。

```bash
docker compose up -d          # postgres · redis · backend · celery worker/beat · market-stream
cd frontend && npm install && npm run dev    # http://localhost:3000
```

**验证 / 排查**

```bash
docker compose ps                                  # 六个容器都应是 Up
docker compose exec -T backend python -c \
  "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"
docker compose logs -f celery-worker               # 引擎跑没跑
docker compose logs -f market-stream               # 实时 1min bar 写入
docker compose down                                # 停掉整套（数据保留）
```

主机端口被本机原生服务占用，所以 compose 改发布：**PostgreSQL 5433** · **Redis 6380** ·
backend 8000（8000 在 IPv4 上可能被本机 php 抢占，健康检查走容器内）。

夜间观察期用 [`scripts/night-watch.sh`](scripts/night-watch.sh) 启停 + 看状态。

---

## 📁 项目结构

```
cloud-ai-trading/
├── quant/                  # 确定性引擎（framework-free）
│   ├── data/               # fetch · store(Parquet) · registry · calendar · bars.get_bars()
│   ├── engine/             # indicators · signal · strategy · funnel · sizing · exits（纯函数）
│   ├── backtest/           # costs · simulator · metrics · walkforward · bias_checks
│   └── research/           # r09 walk-forward 校准
├── backend/
│   ├── app/modules/        # simledger · market · watchlist · auth · llm · nightwatch
│   │                       # · system(watchdog) · notifications · admin
│   ├── app/tasks/          # quant_tasks · telegram_tasks
│   ├── tasks/celery_app.py # Beat 排程
│   └── migrations/         # Alembic
├── frontend/               # Vue 3 + Vite
├── docs/                   # 运营 + 部署；docs/archive/ 是 crypto 时代历史文档
├── scripts/                # night-watch · deploy · cleanup
└── docker-compose.yml
```

---

## ⏱️ Beat 排程（UTC）

| 任务 | 时间 | 作用 |
|---|---|---|
| `quant.signal_cycle` | 21:30 收盘后 | 同步日线 → 跑引擎 → 明日 recommendations + 每日 exit 管理 |
| `quant.entry_cycle` | 每 15 min（任务内 gate 到 RTH） | 按建议为对照账户建仓 |
| `quant.position_cycle` | 每 5 min（gate 到 RTH） | 盘中 stop 检查 |
| `quant.heartbeat` · `quant.telegram_poll` | 每 1 min | 心跳 / Telegram 命令 |
| `market.eod_correction` | 01:30 | 用 SIP 数据校正前一交易日的 1min bars |

---

## 🧪 测试

```bash
make test            # backend + quant，跑在 host venv 上
make test-backend
make test-quant
make test-docker     # 跑在容器里
make lock            # requirements.in 改动后重新冻结 requirements.txt
```

---

## 📚 关键文档

- [`CLAUDE.md`](CLAUDE.md) — 架构地图 + 7 条铁律（改代码前先读）
- [`docs/operations/HOW-CAT-OPERATES.md`](docs/operations/HOW-CAT-OPERATES.md) — 系统每天怎么运转
- [`PROFESSIONAL_QUANT_SYSTEM_ASSESSMENT.md`](PROFESSIONAL_QUANT_SYSTEM_ASSESSMENT.md) — 专业度评估（OOS 口径、fail-closed gates）
- [`quant/README.md`](quant/README.md) — quant 包 setup 与回测
- [`docs/`](docs/README.md) — 部署与运营；[`docs/archive/`](docs/archive/README.md) 是历史文档，别照着实现

---

## 🔐 环境变量

```bash
cp .env.example .env     # 然后至少填 SECRET_KEY / ENCRYPTION_KEY /
                         # ANTHROPIC_API_KEY / REDIS_PASSWORD / ALPACA_* / TELEGRAM_*
```

secrets 只走 `.env`，永远不进 git。自助注册默认关闭（`ALLOW_REGISTER=false`，
`/api/v1/auth/register` 返回 403）；开户走 API 或 psql。
