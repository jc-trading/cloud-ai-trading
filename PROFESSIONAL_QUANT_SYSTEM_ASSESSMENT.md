# 专业量化交易系统评估报告

- Project: `cloud-ai-trading`
- Assessment date: 2026-07-31
- Scope: Quant research, signals, portfolio construction, risk, execution and operations
- Current mode: US stocks/ETF, simulation-only

## 1. 结论

**把系统定位为 Research + Shadow Trading 平台，不要定位成自动赚钱系统，目前不要接真钱。**

系统工程基础不错，但策略 edge 尚未稳定：

| 领域 | 评价 | 判断 |
|---|---|---|
| 数据与研究框架 | B- | 有 point-in-time universe、corporate action、walk-forward |
| Backtest methodology | B- | D-1 signal / D-open fill、成本模型、SPY benchmark |
| Alpha evidence | D+ | 不稳定，无法证明有可部署 edge |
| Portfolio construction | C | 有 sizing、slot、sector cap，缺 factor/beta/correlation 管理 |
| Risk controls | C | 有 stop/pause/halt，但 live-backtest parity 有缺口 |
| Execution simulation | D | 内部 ledger fill，不是真正 broker order lifecycle |
| Operations | B- | 有 Celery、heartbeat、watchdog、Telegram，数据异常未完全 fail closed |
| Live capital readiness | F | 不应连接真钱 |

最大问题不是功能不足，而是：

> Dashboard 的 OOS PF 1.34，并不代表当前固定参数的实际部署策略也有 PF 1.34。

## 2. 专业量化交易员会怎样使用系统

### 2.1 Research：寻找和否定 edge

系统首先是实验室，不是 signal vending machine。每个策略必须回答：

- 收益来自什么市场行为？
- 哪个 component 真正贡献 alpha？
- 在什么 regime 有效或失效？
- 扣除真实成本后是否仍然有效？
- 参数轻微改变后，结果是否立即崩掉？

工作重点是 signal ablation、walk-forward、stress test 和 bootstrap，而不是根据一次 PF/Sharpe 决定上线。

### 2.2 Pre-market：只处理已批准策略

开盘前确认：

- 数据完整、fresh、corporate-action adjusted。
- 当天 strategy/data/code version。
- 持仓、portfolio heat、sector/beta exposure。
- Earnings、gap、停牌等风险事件。
- Pause/halt 状态。

关键数据缺失时必须 fail closed：**不产生订单。**

### 2.3 Intraday：专注执行与风险

Intraday 不改参数，只监控：

- Order accepted / partial fill / rejected / cancelled。
- Spread、slippage、latency。
- Stop 是否成功挂出。
- Cash、buying power、position limit。
- Kill switch 和数据断线。

### 2.4 Post-close：对账与归因

每日自动生成：

- Broker、order、fill、position、cash reconciliation。
- Gross P&L、cost、slippage、gap loss。
- Signal、sector、beta attribution。
- Backtest expected fill vs actual fill。
- 数据和任务异常。

### 2.5 Weekly / Monthly：继续、降级或停用

固定版本观察足够样本后再决定：

- Edge 是否 decay。
- Backtest 与 forward result 的偏差来自 signal、execution 还是 regime。
- 是否降低 exposure 或重新进入 research。
- 是否 retire strategy。

## 3. 策略结果真正说明什么

数据来源：`cat-data/r09/results.json`

### 3.1 OOS 表面结果

- Trades: **269**
- Win rate: **45.7%**
- Profit factor: **1.34**
- Average R: **+0.16**

这些数字只能说明“可能存在弱 edge”，不能证明可部署。

### 3.2 稳定性结果

- 只有 **2/7** OOS windows 在 Sharpe 和 return/max-drawdown 上战胜 SPY。
- Median OOS PF 只有 **1.04**。
- PF 区间为 **0.76–3.26**，regime sensitivity 很强。
- 每个 window 只有约 33–42 trades，统计置信度低。
- 七个 window 使用不同的 calibrated parameters。

### 3.3 当前固定参数表现

当前系统使用固定 consensus parameters。其 full-span illustrative result：

- CAGR: **-1.19%**
- Sharpe: **0.03**
- Max drawdown: **-46.2%**
- PF: **0.96**
- SPY CAGR: **14.84%**

因此 PF 1.34 badge 容易造成误解：

> PF 1.34 来自每个年度分别校准的参数组合，不是当前 fixed deploy configuration 的直接 OOS 成绩。

### 3.4 200-day regime filter

- OOS aggregate PF: **1.02**
- Full-span CAGR: **2.71%**
- Sharpe: **0.24**
- Max drawdown: **-36.7%**

结论：不能继续靠增加简单 filter 修补策略。

## 4. 信号层问题

### 4.1 Confidence 不是成功概率

当前 confidence 是 MACD histogram z-score 经 logistic 转换，再乘 RSI penalty。

- 93 confidence 不等于 93% 胜率。
- 尚未证明 90–100 分组优于 70–80。
- 尚未证明 confidence 与未来 return 单调。

必须评估 rank IC、decile return、precision 和 calibration curve。

### 4.2 Phase classifier 方向失真

20d 和 60d 结果中，`down` phase 后续 median return 反而高于 `up`，separation 为负。

当前 phase 只能作为图表描述，不能参与交易决策。

### 4.3 Support / resistance zones 没有 edge

触碰后被尊重：

- Resistance: 约 **20%**
- Support: 约 **25%**

多数 zone 会被突破，当前不适合用于 entry、stop 或 target。

## 5. 优化优先级

### P0：测量可信度

1. 建立 stitched OOS equity curve，报告 CAGR、Sharpe、MaxDD、turnover、exposure、beta、alpha 和 SPY。
2. Dashboard 只展示当前部署参数的成绩；window-specific calibrated result 必须分开。
3. 修复 live/backtest parity：pyramid combined-risk、stale bars、cash concurrency、fill/cost/stop。
4. 所有 market data 必须带 as-of timestamp、expected session、completeness、source、stale reason。
5. 保存 immutable experiment record：data version、code SHA、parameters、universe snapshot、result。

### P1：重新验证 alpha

1. 对 MA、MACD、RSI、ATR、sector cap、confidence threshold 做 ablation。
2. 把 confidence 改成可验证的 cross-sectional score，检查分组收益是否单调。
3. 暂停将 phase/zones 当作有效信号，直到独立通过 OOS gate。
4. 增加低相关 alpha：relative strength、short-term mean reversion、earnings drift、cross-sectional momentum。
5. 优化目标改为多 window 稳定性、邻近参数一致性、成本压力测试和 bootstrap confidence interval。

### P2：组合、执行与上线纪律

1. 增加 beta、factor、sector、correlation、gross exposure、portfolio heat、earnings-gap controls。
2. 使用真正 Alpaca paper orders 跑完整 order lifecycle。
3. 记录 bid/ask、arrival price、fill price、latency、partial fill、reject reason。
4. 用 paper observations 校准 cost model，不长期依赖固定 5 bps slippage。
5. 按 shadow → broker paper → 极小资金 pilot 顺序推进。

## 6. 建议上线 Gate

### 进入长期 paper trading

- Exact deployed configuration 有独立 stitched OOS result。
- 至少 70% walk-forward windows 为正。
- Median window PF ≥ 1.15。
- 双倍成本压力测试后仍为正。
- Parameter neighborhood 不出现大面积反转。
- 无 critical data/parity/ledger finding。

### 进入真钱 pilot

- 至少 6 个月且 ≥50 个 closed paper trades，取较迟者。
- Paper 与 backtest 的 slippage、turnover、holding period 在预设 tolerance 内。
- 每日 broker-ledger reconciliation 为零差异。
- Kill switch、duplicate-order、stale-data、network-retry 演练通过。
- 从极小 capital allocation 开始，确认后逐级扩大。

## 7. 当前正确使用方式

- 保持 system account 完全自动、不可人工干预。
- Manual practice account 独立记录个人判断。
- 固定策略版本收集 forward sample。
- 不根据单日 shortlist 直接下注。
- 优先证明 measurement integrity 和 repeatable edge，再扩展 AI、dashboard 或 live execution。

## 8. 参考

- Local backtest result: `cat-data/r09/results.json`
- Local regime test: `cat-data/r09-regime200/results.json`
- FINRA: <https://www.finra.org/rules-guidance/notices/15-09>
- SEC: <https://www.sec.gov/rules-regulations/staff-guidance/trading-markets-frequently-asked-questions/divisionsmarketregfaq-0>
- Alpaca paper trading limitations: <https://docs.alpaca.markets/us/docs/paper-trading>
