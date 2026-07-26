# ☁️ Cloud AI Trading System

**一个实时的加密货币交易信号生成系统，基于技术分析和市场数据**

---

## 🚀 快速开始

### 启动系统

**前提**：先启动 Docker Desktop，等它的 daemon 就绪。

所有命令都从项目根目录 `cloud-ai-trading/` 开始。

**① 启动后端栈**（postgres / redis / backend / celery-worker / celery-beat 五个容器）

```bash
cd cloud-ai-trading
docker compose up -d
```

**② 启动前端**（另开一个终端窗口 —— 前端 dev server 会占着这个窗口）

```bash
cd cloud-ai-trading/frontend
npm install        # 仅首次需要
npm run dev        # 跑在 http://localhost:3000
```

**③ 打开看板**

浏览器访问 **http://localhost:3000** → 登录（或右下 Sign Up 注册）。落地页就是统一的 Decision Feed（可按 ALL / CRYPTO / EQUITY 切换）。

**验证 / 排查**

```bash
docker compose ps                      # 五个容器都应是 Up (healthy)
curl http://localhost:8000/api/health  # 应返回 200
docker compose logs -f backend         # 后端日志
docker compose logs -f celery-beat     # 定时任务排程日志
docker compose down                    # 停掉整套（数据保留）
```

详见 [docs/setup/Quick-Start.md](docs/setup/Quick-Start.md)

---

## 📊 系统架构

### 核心组件

| 组件 | 技术 | 功能 |
|------|------|------|
| **后端** | Python 3.12 + FastAPI | RESTful API + WebSocket 实时数据 |
| **数据库** | PostgreSQL 16 | 存储行情、指标、信号、头寸 |
| **缓存/队列** | Redis 7 | Celery 任务队列 + 缓存 |
| **任务调度** | Celery 5.4 + Beat | 每分钟数据收集和信号生成 |
| **前端** | Vue.js | 交易信号仪表板 |

### 数据流

```
Binance WebSocket 
    ↓
OHLCV Candles (1分钟)
    ↓
Technical Indicators (RSI, MACD, Bollinger Bands)
    ↓
Trading Signal Generator (Momentum + Contrarian)
    ↓
Portfolio Manager (位置 + P&L 跟踪)
    ↓
Telegram Notifications (实时警报)
```

详见 [docs/project/System-Architecture.md](docs/project/System-Architecture.md)

---

## 📁 项目结构

```
CloudAiTrading/
├── docs/                          # 📚 所有项目文档
│   ├── project/                   # 项目规划和架构
│   │   ├── System-Architecture.md
│   │   ├── Functional-Spec.md
│   │   └── Project-Progress.md
│   ├── setup/                     # 部署和配置
│   │   ├── Quick-Start.md
│   │   ├── Installation.md
│   │   └── Deployment.md
│   ├── implementation/            # 实现细节
│   │   ├── Frontend-Architecture.md
│   │   └── backend/               # 后端具体文档
│   ├── operations/                # 运营文档
│   │   ├── Session-Summary.md
│   │   └── Monitoring-Report.md
│   └── audit/                     # 代码审计报告
│       └── Code-Audit-Report.md
│
├── backend/                       # 🐍 Python FastAPI 后端
│   ├── app/                       # 应用核心代码
│   │   ├── api/                   # REST API endpoints
│   │   ├── models/                # SQLAlchemy ORM
│   │   ├── services/              # 业务逻辑
│   │   └── tasks/                 # Celery 任务
│   ├── migrations/                # 数据库迁移
│   ├── tests/                     # 测试
│   └── requirements.txt           # 依赖
│
├── frontend/                      # 🎨 Vue.js 前端
│   ├── src/
│   │   ├── components/            # Vue 组件
│   │   ├── views/                 # 页面
│   │   ├── api/                   # API 客户端
│   │   └── stores/                # 状态管理
│   └── public/
│
├── docker/                        # 🐳 Docker 配置
│   ├── nginx/                     # Nginx 反向代理
│   └── postgres/                  # PostgreSQL 初始化
│
├── scripts/                       # ⚙️ 运营脚本
│   ├── deploy.sh                  # 部署脚本
│   └── cleanup.sh                 # 清理脚本
│
├── docker-compose.yml             # Docker 编排配置
├── .env                           # 环境变量（敏感信息）
├── .env.example                   # 环境变量模板
└── .gitignore
```

---

## ✅ 当前开发阶段

| 阶段 | 状态 | 说明 |
|------|------|------|
| **Phase 1:** 认证 & 用户管理 | ✅ 完成 | JWT, RBAC, Watchlists |
| **Phase 2:** 市场数据 | ✅ 完成 | Binance WebSocket, 技术指标 |
| **Phase 3:** 交易信号 | ✅ 完成 | 动量策略, 投资组合管理, Telegram 通知 |
| **Phase 4:** 前端仪表板 | 🔄 进行中 | 实时信号和头寸显示 |
| **Phase 5:** 自动交易执行 | ⏳ 计划中 | Binance 现货交易 API |

详见 [docs/project/Project-Progress.md](docs/project/Project-Progress.md)

---

## 🔧 核心功能

### ✨ 已实现
- ✅ 实时市场数据收集（Binance WebSocket）
- ✅ 技术指标计算（RSI, MACD, Bollinger Bands）
- ✅ 交易信号生成（动量 + 反向策略）
- ✅ 投资组合管理（头寸跟踪、P&L 计算）
- ✅ Telegram 实时通知
- ✅ RESTful API

### 🚧 开发中
- 🔄 Web 仪表板（Vue.js）
- 🔄 更多交易策略

### 📋 计划中
- ⏳ 自动交易执行
- ⏳ 高级风险管理
- ⏳ 历史数据分析

---

## 📚 文档导航

### 快速参考
- **首次部署？** → [docs/setup/Installation.md](docs/setup/Installation.md)
- **启动现有系统？** → [docs/setup/Quick-Start.md](docs/setup/Quick-Start.md)
- **想了解架构？** → [docs/project/System-Architecture.md](docs/project/System-Architecture.md)
- **遇到问题？** → [docs/operations/Monitoring-Report.md](docs/operations/Monitoring-Report.md)
- **代码审计？** → [docs/audit/Code-Audit-Report.md](docs/audit/Code-Audit-Report.md)

### 详细文档
- [项目规划](docs/project/) - 系统设计、功能规格、进度跟踪
- [部署指南](docs/setup/) - 安装、配置、运维
- [实现文档](docs/implementation/) - 架构细节、验证过程
- [运营手册](docs/operations/) - 监控、故障排除、Session 总结
- [审计报告](docs/audit/) - 代码质量、问题修复

---

## 🎯 常用命令

```bash
# 启动整个系统
docker compose up -d

# 查看服务状态
docker compose ps

# 查看日志
docker compose logs -f backend
docker compose logs -f celery_worker
docker compose logs -f celery_beat

# 重启服务
docker compose restart backend
docker compose restart celery_worker

# 进入数据库
docker compose exec postgres psql -U postgres -d cloudaitrading

# 停止系统
docker compose down

# 完全清空（谨慎！）
docker compose down -v
```

---

## 📊 监控面板

### 系统健康检查
```bash
# API 健康
curl http://localhost:8000/api/health

# 最近的交易信号
curl http://localhost:8000/api/signals?limit=5

# 当前投资组合
curl http://localhost:8000/api/portfolio
```

### 数据库查询
```bash
# 最新 K 线
SELECT * FROM ohlcv_candles ORDER BY created_at DESC LIMIT 10;

# 最新信号
SELECT * FROM trading_signals ORDER BY created_at DESC LIMIT 10;

# 开仓头寸
SELECT * FROM positions WHERE status = 'open';

# 投资组合统计
SELECT * FROM portfolio_stats ORDER BY updated_at DESC LIMIT 1;
```

---

## 🔐 环境变量配置

```bash
# 复制模板
cp .env.example .env

# 编辑 .env 并填入：
# - TELEGRAM_BOT_TOKEN
# - TELEGRAM_CHAT_ID
# - BINANCE_API_KEY / BINANCE_API_SECRET
# - DATABASE_URL
# - REDIS_URL
```

---

## 🐛 故障排除

| 问题 | 解决方案 |
|------|---------|
| API 不响应 | `docker compose restart backend` |
| 没有收到 Telegram 通知 | 检查环境变量，查看 celery_worker 日志 |
| 数据库连接错误 | 确保 PostgreSQL 容器运行，检查 DATABASE_URL |
| Celery 任务未执行 | `docker compose restart celery_beat celery_worker` |
| 性能下降 | 检查 Redis 内存，清理历史数据 |

详见 [docs/operations/Monitoring-Report.md](docs/operations/Monitoring-Report.md)

---

## 👨‍💻 开发

### 后端开发
```bash
cd backend
pip install -r requirements.txt
python -m pytest tests/
```

### 前端开发
```bash
cd frontend
npm install
npm run dev
```

---

## 📝 最后更新

- **最后维护:** 2026-04-14
- **当前版本:** 1.0.0 (Phase 3)
- **系统状态:** ✅ 运行中 (实时数据收集和信号生成)

详见 [docs/operations/Session-Summary.md](docs/operations/Session-Summary.md)

---

## 📖 更多信息

- [下一个 Session 快速启动](docs/operations/Next-Session-Quickstart.md)
- [功能规格书](docs/project/Functional-Spec.md)
- [后端快速开始](docs/setup/Backend-Quick-Start.md)
- [部署清单](docs/operations/Ready-to-Deploy.md)

---

**Made with ❤️ by Cloud AI Trading Team**
