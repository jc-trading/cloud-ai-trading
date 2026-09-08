# CAT 现行操作手册 — 系统实际怎么选股、分析、进出场、加减仓

> 口径：**代码现状**（截至 2026-08-15,`main` @ `2932657` + working tree)。
> 每条规则都指到文件行号,不写设计意图、不写还没接线的东西。
> 唯一自动交易的账户是 **`system-对照`**(`is_system=true`);用户的 practice 账户
> 全部手动下单,不受本文任何自动逻辑影响。

---

## 0. 一句话

> 每天收盘后用一条**纯确定性**的量化漏斗,从 S&P500 最活跃的 ~20% 里选出
> **最多 10 只**明天的候选;第二天盘中每 15 分钟看一次价格,**不追高**地把候选
> 买进最多 3 个槽位;持仓靠 **2×ATR 硬止损 + 3×ATR 保护性移动止损**管理,
> 满足条件就**整仓卖出**(没有部分减仓);LLM 只写解释文字,**不碰任何决策**。

---

## 1. 一天的时间轴

Beat 全部按 **UTC** 排程(`backend/tasks/celery_app.py:95-125`),任务内部再用
**ET(XNYS 日历)**自我把关。MYT = UTC+8。

| UTC | ET | MYT | 跑什么 | 频率 |
|---|---|---|---|---|
| 13:30–20:00 | 09:30–16:00 | 21:30–04:00 | `quant.position_cycle` 破位检查 | 每 5 分钟 |
| 13:30–20:00 | 09:30–16:00 | 21:30–04:00 | `quant.entry_cycle` 进场/加仓 | 每 15 分钟 |
| 21:30 | 17:30 | 05:30 | `quant.signal_cycle` 选股 + 日终出场 + 快照 | 每日一次 |
| 每分钟 | — | — | `quant.heartbeat` · `quant.telegram_poll` | 60s |

三个 cycle **各自**再判一次 `qcal.is_trading_day()` / `in_rth()`,非交易日或非
RTH 直接返回 `skipped`——beat 排了不代表会动。

> ⚠️ `cycles.py:125` 的 `ENTRY_WINDOW_ET = ((9,30),(10,10))` 是 **v3.1 之后的死代码**,
> 没有任何地方引用。真实进场窗口 = 整个 RTH。

---

## 2. 选股 — 第一步:今天分析哪些股(universe)

`backend/app/tasks/quant_tasks.py:194-234`

**同步池(sync set)** — 只为了让流动性排名新鲜,不代表会分析:

```
PIT S&P500 成分(constituents_on(today))   ~503
  ∪ ETF 白名单 SPY / QQQ
  ∪ 当前持仓(哪怕已被踢出指数)
  ∪ 对照账户 owner 的 stock watchlist
= 507 只 → Alpaca SIP 日线增量同步(分批,失败自动逐只回退)
```

**分析池(analyzed universe)** — 真正跑信号的:

```
top_liquid(指数成分, 20%)                  ~101   ← 20 日平均「股数」成交量排序
  ∪ SPY / QQQ
  ∪ watchlist(无视流动性门槛,「我关注的永远在池子里」)
  ∪ 当前持仓
≈ 106 只
```

要点:
- 用 **point-in-time 成分**(`quant/data/universe.py:61-74`),不是今天的名单 → 无幸存者偏差。
- 流动性排名用**股数**成交量(`universe.py:97-112`),funnel 里的 $20M 门槛才是**金额**。
- 持仓永远同步 + 永远分析,否则退市/剔除的票 bars 会冻住,出场逻辑管理的是「鬼」。

**Fail-closed(A2)**:同步失败 > 20% → **一条推荐都不发**
(`quant_tasks.py:240`),第二天 entry_cycle 因为查不到 recommendation 行而
拒绝下单。出场、保护、快照照跑。

---

## 3. 分析 — 每只股算什么

`quant/engine/strategy.py:49-87`,四个指标**角色不同、不等权**:

| 指标 | 参数 | 角色 | 怎么用 |
|---|---|---|---|
| MA | 5 / 20 | **方向闸门** | `up` = close>MA20 且 MA5>MA20;`down` = 两个都反过来;其余 `flat` |
| MACD hist | 12/26/9 | **动量 → 信心分** | 对 hist 取 60 日滚动 z-score → logistic → (0,1) |
| RSI | 14 | **过热惩罚** | RSI>70 开始扣分,系数 `1-(RSI-70)/30`,地板 0.2 |
| ATR | 14 | **波动尺** | `stop_distance = 2×ATR` · `expected_move = 1×ATR` |

```
confidence = 100 × logistic(zscore(MACD_hist, 60)) × rsi_factor
             ↑ 非 up 方向直接置 0(strategy.py:83)
```

**Phase(趋势阶段)** 是另一套、只用来描述,不参与选股打分
(`quant/engine/phase.py:55-71`):`up` / `down` / `range` / `unknown`,
MA20 斜率带 0.1% 死区,避免噪声来回翻标签。
产品口径:**phase 是现状描述,不是预测**,文案禁止暗示涨跌。

数据口径:`get_bars()` 是唯一数据入口,磁盘存 RAW、**读的时候**做拆股/分红复权
(`quant/data/bars.py`)。若某只票没缓存 corporate actions 又出现 >50% 单日跳空,
会 WARN 并返回 RAW 价格——今天早上 HOOD 就是这个情况。

---

## 4. 选股 — 漏斗(funnel)→ 明天的 shortlist

`quant/engine/funnel.py:35-69`,顺序固定,每层可解释:

```
分析池的 feature 表
 ├─ 剔除 ETF 白名单(SPY/QQQ 走独立配额,不进股票池打分)
 ├─ 流动性:  20 日 ADV > $20,000,000  且  price > $5
 ├─ 波动率:  1% ≤ ATR/price ≤ 8%        (太死赚不到,太野会被扫止损)
 ├─ 趋势对齐: above_rising_ma20 且 direction == "up"   ← 只做多
 ├─ 信心门槛: confidence ≥ 65            ← 部署值,不是 config 默认的 0
 ├─ 按 confidence 降序排序
 ├─ 行业上限: 每个 sector 最多 2 只
 └─ 取前 10 只  → shortlist(rank 1..N)
```

**ETF 单独一条线**(`funnel.py:72-81`):SPY/QQQ 里符合 `above_rising_ma20 且 up`
的,按 confidence 取 **最多 1 只**,追加到 shortlist 尾部。它**不占股票槽位**。

**存进 recommendations 表的是**(`cycles.py:230-242`):
shortlist 的名字 **+ 所有 direction=="up" 的名字**(watch-only,`shortlist_rank=null`)。
所以你会看到「73 条推荐,其中 shortlist 只有几只」——73 是 feed,可买的是带 rank 的那几条。

`trade_date` = `qcal.next_session(today)`(`cycles.py:228`),**跳过周末与假期**。
发布是幂等的:同一 trade_date 整批 delete 再 insert(`cycles.py:246-255`)。

Sector 来自 Finnhub 缓存;**取不到的一律记 `unknown`**,而 `unknown` 会被当成
一个 sector 一起吃那个「每 sector 2 只」的上限 —— 缓存缺失会隐性收窄 shortlist。

---

## 5. 进场 — 要不要买、买多少

`cycles.py:325-435` + `quant_tasks.py:129-175`。每 15 分钟一轮,门禁**从外到内**:

**① 保护层门禁**(任一命中 → 整轮不买,`cycles.py:148-161`)
```
runtime/HALT 哨兵文件存在        → 拒绝(DB 挂了也拦得住)
state.halted 且未过冷却期        → 拒绝(15% 回撤 halt)
state.paused_until >= today      → 拒绝(-2% 日亏 pause / 手动 /pause)
```

**② Fail-closed**:今天没有任何 recommendation 行 → `fail-closed: no recommendations`,
一单不下。

**③ 逐只候选(按 shortlist_rank 顺序)**
```
报价可用? Finnhub quote 存在、>0、且 ≤15 分钟旧      否 → 跳过(绝不拿坏数据下单)
stop_distance > 0 ?                                  否 → 跳过
不追高: quote ≤ 参考价 ×(1+3%) ?                     否 → 跳过,等下一个 15 分钟回调
       (参考价 = 昨收,存在 rec.features.price)
槽位还有?  股票: 已持股票数 < slots
           ETF : 已持 ETF 数 < 1
           已持有该票 → 走加仓判断(见 §6)
```

**④ 定价与止损**(和回测同源的成本模型)
```
entry_eff = quote × (1 + slippage 5bps + half-spread)
            half-spread 按 ADV 分层: ≥$500M→2bps · ≥$100M→4bps · 其余→8bps
stop      = entry_eff − 2×ATR
```

**⑤ 下单股数 = 四个 cap 取最小**(`quant/engine/sizing.py:26-50`)
```
① 风险预算:  equity × 3%  ÷ (entry_eff − stop)
② 槽位金额:  equity ÷ slots ÷ entry_eff
③ 可用现金:  cash ÷ entry_eff
④ 流动性:    ADV × 1% ÷ entry_eff
支持碎股(fractional=True),$2,000 分 3 个槽必须靠它
```

**槽位阶梯**(按当前 equity 取档,`sizing.py:16-23`):
`<$5k → 3 槽` · `≥$5k → 4` · `≥$10k → 5` · `≥$20k → 10`。
现在 equity ≈ $2k → **3 个股票槽 + 1 个 ETF 槽**。

**⑥ 幂等**:`entry:{account}:{symbol}:{date}` —— 同一只票**一天只可能进一次**,
15 分钟跑 26 轮也不会重复下单。现金不够就 `continue` 跳过这只,不炸整轮。

> ⚠️ **盘中择时这一段没有回测支撑**。R0-9 / fixed_oos 建模的是「次日开盘一次性成交」;
> 「每 15 分钟 + 不追高 3%」是 v3.1 的 live-only 行为,是待校准的起始值。
> 对照账户的成绩单在这里会和回测分道扬镳。

---

## 6. 加仓 — 什么时候加,加完怎么办

`sizing.py:53-56` + `service.py:148-161`。金字塔加仓,**只加赢家**:

```
允许加仓 ⟺  当前 entry_eff > 该仓位 avg_cost   且   adds_done < 1
```

- **每只票一生只能加 1 次**(`MAX_PYRAMID_ADDS_PER_SYMBOL = 1`)。
- 加仓走的是同一条 entry_cycle 通路 —— 一样要过保护层、报价新鲜度、不追高 3%、四个 cap。
- 加仓**不占新槽位**(它已经在 `held` 里)。
- 加完立刻重算(`service.py:148-161`):
  ```
  avg_cost  = 股数加权混合
  stop      = max(旧 stop, 新算 stop, 让「总股数×(avg_cost − stop)」≤ equity×3% 的最低 stop)
              → 止损只升不降,加仓后组合风险仍锁在 3% 预算内
  adds_done += 1
  ```

**没有「减仓」这个动作。** 系统只有全仓买入/加仓/**整仓卖出**三种,
rev2 明确不做部分止盈(`service.py:176-182`)。要减小暴露只能整只平掉。

---

## 7. 出场 — 两条完全不同的通路

### 7.1 盘中(每 5 分钟)— 只认止损被击穿

`cycles.py:492-514`

```
for 每个持仓:
    报价不可用(缺失/≤0/>15 分钟旧) → 跳过,什么都不做
    quote ≤ 当前 stop → 立刻按 quote 价整仓卖出
                        标签: stop ≥ avg_cost → "trailing" ,否则 "hard_stop"
```

**盘中绝不上抬 trailing stop**(回测 F2 铁律)——移动止损只用日终信息,
否则日内噪声会把止损顶上去然后被自己扫掉。

### 7.2 盘后(signal_cycle 里)— 完整出场栈

`cycles.py:258-320`,顺序**严格复刻回测 simulator**:

```
1. 拉当日日线;若最后一根 bar 的日期 ≠ 今天 → 跳过这只(同步失败,不拿旧 bar 重复折算)
2. maybe_raise_trailing(用 D-1 的 ATR、D-1 的 high_water)   ← 先用「昨天的信息」抬止损
3. update_position_bar(折入 D 这根 bar: high_water / reversal_count / bars_held)
4. evaluate_exit(...)  ← 按优先级取第一个命中的
```

**移动止损的抬升规则**(`exits.py:74-79`):
```
若 未实现盈利(按 high_water 算) ≥ 1.5R:
    candidate = high_water − 3×ATR
    stop = max(stop, candidate)      ← 只升不降
```
这就是**主要的止盈手段** —— 系统**没有固定目标价**,靠 trailing 把利润锁走。

**`evaluate_exit` 的优先级**(`exits.py:82-118`),第一个命中就出:

| # | 出场类型 | 触发条件 | 成交价 |
|---|---|---|---|
| 1 | `hard_stop` / `trailing` | 当日 **low ≤ stop** | `min(stop, open)` —— 跳空穿越诚实地按开盘价成交,不假装止损价 |
| 2 | `reversal`(持续型) | `reversal_count ≥ 3`,即连续 3 根 bar 都是「方向 down **且** close < MA20」 | 当日收盘 |
| 3 | `reversal`(预判型) | 方向 down,且 `close − 1×ATR < avg_cost` 但 `close > avg_cost` | 当日收盘 |
| 4 | `stagnation` | `bars_held ≥ 30` 且方向不是 up | 当日收盘 |

第 2 条的「连续 3 根」就是**真反转 vs 回调的滞回**:掉一天不算,连着掉三天才认。
第 3 条是「还在赚,但下一个 ATR 幅度就会跌破成本」→ 先把利润保住。

出场也是幂等的:`exit:{position_id}` —— 盘中已经平掉的仓位,盘后再跑不会重复卖。

未命中任何出场 → 把 `stop / high_water / reversal_count / bars_held` 写回数据库。
某只票算炸了只跳过它,**绝不让一只坏票回滚整晚的事务**(`cycles.py:317-319`)。

---

## 8. 保护层 — 什么时候整体停手

`cycles.py:440-487`,每晚 signal_cycle 结算一次,状态落库(重启不丢)。

| 机制 | 触发 | 效果 | 解除 |
|---|---|---|---|
| 日亏 pause | 当日 equity 相对昨日快照 ≤ **−2%** | `paused_until` = 下一个交易日 → 挡**新进场** | 自动过期;只延长不缩短(手动 /pause 30 天不会被自动的一天覆盖掉) |
| 回撤 halt | equity ≤ 峰值 × **(1−15%)** | `halted=true`,冷却 **30 天** | 到期自动解除,并**把峰值基线重置为当前 equity**(模拟人工复盘后重启) |
| HALT 哨兵 | `/kill` 写 `runtime/HALT` 文件 | 拒绝一切进场,**DB 挂了也拦得住** | `/resume` 删文件 |

**铁律:保护层只挡进场,永远不挡出场。** 止损、trailing、reversal、stagnation
在 pause/halt/kill 期间照常执行。

Telegram 指令(`app/tasks/telegram_tasks.py`,每分钟轮询):
`/status`(容器/心跳/持仓/保护状态) · `/pause`(30 天) · `/resume` · `/kill`。

**watchdog**(backend 进程内,每 5 分钟,独立于 worker 存活)告警:
worker 心跳 >5 分钟 · RTH 中 position_cycle 停滞 · signal_cycle >26 小时没跑 ·
**持仓没有可用 stop(最高级)** → Telegram。

---

## 9. LLM 在哪里 — 以及不在哪里

`backend/app/modules/llm/explain.py`

- signal_cycle 发布完推荐后,对 **top 10** 各调一次 Claude Haiku,写一两句
  「这个信号现在在读什么」的解释文字。
- system prompt 明确要求:**不预测价格、不给投资建议、'down' 只是现状描述**。
- 所有调用走唯一入口 `call_llm` → 记进 `llm_calls` 表(tokens + 当时单价快照 + USD)。
  失败**永不 raise**,没有 API key 自动跳过,解释留空。

> **LLM 不参与任何交易决策(拍板 A)。** 选股、打分、进场、出场、加仓
> 全部由 `quant/` 里的纯函数决定,和回测共用同一套代码(架构铁律①)。

---

## 10. 现行参数总表(部署值)

| 类别 | 参数 | 值 | 出处 |
|---|---|---|---|
| 宇宙 | 分析池 = 指数 top | **20%**(20 日均股数) | `config.LIQUIDITY_TOP_PCT` |
| 漏斗 | 最低 ADV / 股价 | **$20M / $5** | `config.MIN_AVG_DOLLAR_VOLUME` |
| 漏斗 | ATR/price 区间 | **1% – 8%** | `FunnelParams` |
| 漏斗 | **信心门槛** | **65**(config 默认 0,live 覆盖) | `cycles.py:53` |
| 漏斗 | 每 sector 上限 / shortlist 上限 | **2 / 10** | `config` |
| 漏斗 | ETF 白名单 / 配额 | **SPY,QQQ / 1 槽** | `config` |
| 信号 | MA · MACD · RSI · ATR | **5/20 · 12/26/9 · 14 · 14** | `config` |
| 信号 | z-score 窗口 / RSI 过热线 | **60 / 70(地板 0.2)** | `StrategyParams` |
| 风控 | 单笔风险 | **equity 3%** | `config.PER_TRADE_RISK_PCT` |
| 风控 | 止损距离 | **2×ATR** | `StrategyParams.stop_atr_mult` |
| 风控 | 槽位阶梯 | **3 / 4 / 5 / 10**($2k/5k/10k/20k) | `config.POSITION_LADDER` |
| 进场 | 不追高上限 | **+3%**(未校准) | `config.INTRADAY_ENTRY_CHASE_CAP` |
| 进场 | 报价新鲜度 | **15 分钟** | `cycles.QUOTE_STALE_SECONDS` |
| 出场 | trailing 启动 / 距离 | **1.5R / 3×ATR** | `ExitParams` |
| 出场 | 反转确认 / 停滞门槛 | **连续 3 根 / 30 根 bar** | `ExitParams` |
| 加仓 | 每票最多加 | **1 次** | `config.MAX_PYRAMID_ADDS_PER_SYMBOL` |
| 保护 | 日亏 pause / 回撤 halt / 冷却 | **−2% / −15% / 30 天** | `config` + `cycles.py:444` |
| 成本 | 滑点 + 半价差 | **5bps + 2/4/8bps(按 ADV)** | `quant/backtest/costs.py` |

---

## 11. 诚实清单 — 现在已知的缺口

1. **盘中择时未回测**。fixed_oos 建模「次日开盘一次性进场」;live 是「每 15 分钟 + 不追高 3%」。
   两者不等价,对照账户的成绩单在这一段不能直接和回测比。
2. **`master_settings` 表建了但没接线**。所有参数直接来自 `quant/config.py` 常量
   + `cycles.py` 的 `RECOMMENDED_*`;设计里说的「运行时收紧-only」目前**不生效**,
   改参数 = 改代码重启。
3. **stagnation 的 benchmark 门槛没接**。`daily_exit_management` 不传
   `benchmark_return_since_entry`,所以走的是「持满 30 根 + 信号不再 up 就出」的
   简化分支,没有和 SPY 比。
4. **`ENTRY_WINDOW_ET` 是死代码**,注释还写着「只在 09:30–10:10 进场」,实际是整个 RTH。
5. **部署参数的 OOS 成绩 ≈ 打平**:stitched PF **0.91** / avg R +0.01 / CAGR 2.2% /
   maxDD −38.6%。**平台目前没有已证明的 alpha**,对照账户是诚实实验,不是盈利承诺。
6. **zones(顶/底区间带)不上线** —— 触后守住率只有 20–25%,未达标。
7. **corporate actions 需要人工补**。没缓存的票会在 >50% 单日跳空时 WARN 并用 RAW 价
   (2026-08-15 的 HOOD)。修法:`python -m quant.data.corporate_actions`。
8. **每晚靠这台 Mac 开机跑**。断档 5/12 晚 → equity 曲线有洞,严格口径下 G2 计时已脏;
   R3 VPS 是拿到干净结论的前置条件。

---

## 12. 一张图收尾

```
D 日 17:30 ET ─ signal_cycle
   │  同步 507 只日线 ──(失败>20% → 什么都不发)
   │  分析 ~106 只 → MA/MACD/RSI/ATR → confidence + phase
   │  漏斗: 流动性 → 波动率 → 趋势对齐 → conf≥65 → sector≤2 → top10 (+1 ETF)
   │  发布 D+1 的 recommendations(shortlist 有 rank,其余 watch-only)
   │  日终出场: 抬 trailing(用 D-1 信息) → 折入 D 的 bar → 止损/反转/停滞
   │  更新保护状态 → 快照 equity → top10 交给 Haiku 写解释 → Telegram
   ▼
D+1 盘中 ─ 每 15 分钟 entry_cycle: 保护层 → 报价新鲜 → 不追高 → 槽位 → 四 cap 定量 → 买
        └ 每 5 分钟 position_cycle: quote ≤ stop → 整仓卖(绝不盘中抬 stop)
```
