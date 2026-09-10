# docs/archive — 历史文档

这里的文档描述的是 **已经不存在的系统**：v1/v2 的 Binance/crypto 数据面（migration
013 删表 + R1-8 删代码）和 P0–P3 时代 parked 的 equity/execution/analysis/risk/
strategy/trading/exchange/fundamentals 模块（Phase B 删代码 + migration 017 删表）。
留着只为回溯当时的判断，**不要照着实现**。

当前系统的入口是根目录 `README.md` 和 `CLAUDE.md`。

| 目录 / 文件 | 归档原因 |
|---|---|
| `root-reports/P2_*.md`, `root-reports/P2_REVIEW_SUMMARY.txt` | P2 架构评审与修复记录，评审对象是 crypto 信号管线 |
| `root-reports/P3_*.md` | P3 风控/tracker 设计与完工报告，对应模块已在 Phase B 删除 |
| `root-reports/PROJECT_STATUS.md` | 停更于 2026-04-14，描述 Phase 1–4 的 crypto 系统 |
| `code-review/` | P0/P1 时代的 code review 与审计报告，被审的代码已删 |
| `implementation/` | P0–P2 各阶段实现文档（Binance 采集、4 策略信号、Claude 分析） |
| `project/` | v1 架构 / 功能规格 / 进度 / 成本文档，写于 Direction v3 之前 |
| `setup/Installation.md` | MVP v1 安装指南，步骤里仍要 Binance key、查 `ohlcv_candles` |
| `operations/NEXT_SESSION_QUICK_START.md` | 交接笔记，交接的是 crypto 管线 |
| `operations/READY_TO_DEPLOY.md` | crypto 上线检查单（BTCUSDT/ETHUSDT 验证日志） |
| `operations/SESSION_SUMMARY.md` | v1 session 总结（binance_client 修复记录） |
| `operations/SYSTEM_MONITORING_*.md` | Phase 1 主机监控栈的部署/修复报告；该栈已在 Phase B 降级删除 |
| `setup/DEPLOY_INSTRUCTIONS.md`, `setup/DEPLOY_PHASE_1_CHECKLIST.md` | 同一个 Phase 1 监控栈的部署步骤与检查单，验收项全是已删的 `/api/system/*` + `ws/system/logs` |
