# Recommendation outcomes — forward returns & confidence IC

> Generated 2026-08-12 09:15 MYT by `python -m app.modules.simledger.outcomes`
> (run #2 — absorbed the 08-11 recs after the 05:30 signal_cycle synced their
> session bar). Idempotent; re-run any night to refresh. Pending 65 = the
> 08-12 recs, evaluable after tonight's bars land.

- recommendations in DB: 552; evaluated: 487 (rest pending: trade_date bar not stored yet)
- directions: {'up': 487}

## Coverage per trade_date

```
trade_date   n  with_1d  with_3d  with_5d
2026-07-31 256      256      255      255
2026-08-04  52       52       52       52
2026-08-06  58       58       58        0
2026-08-07  58       58        0        0
2026-08-11  63        0        0        0
```

## Confidence IC (Spearman, direction=up, n=487)

- ret_1d: IC = +0.104 (n=424)
- ret_3d: IC = +0.069 (n=365)
- ret_5d: IC = +0.127 (n=307)

## Mean forward return by confidence quintile (direction=up)

```
           n  conf_min  conf_max  ret_1d  ret_3d  ret_5d
quintile
1         85  +13.8480  +49.0840 +0.0020 +0.0046 +0.0034
2         85  +49.3790  +64.5210 +0.0059 +0.0152 +0.0230
3         84  +64.5230  +73.1920 +0.0171 +0.0280 +0.0378
4         85  +73.2760  +81.0790 +0.0148 +0.0280 +0.0353
5         85  +81.1960  +95.7720 +0.0094 +0.0151 +0.0268
```

## Shortlist vs below-the-cut (direction=up, mean returns)

```
           ret_1d        ret_3d        ret_5d
             mean count    mean count    mean count
bucket
below_cut +0.0098   414 +0.0186   358 +0.0254   303
shortlist +0.0096    10 -0.0050     7 +0.0065     4
```

## Read (2026-08-12, run #2 — n 变大后结论没变)

1. **IC 弱正且稳定**:run #1 → #2 数字几乎不动(1d +0.104 不变,3d/5d 微升),
   confidence 排序有信息但弱(IC ~0.1)。
2. **顶部不单调坐实了一分**:Q3/Q4(conf 65-81)仍然最好,Q5(81+)仍然回落 —
   两轮数据同形。「93 分 ≠ 93% 胜率」。
3. shortlist 样本涨到 n=10/7/4,仍跑输 below-cut(5d +0.65% vs +2.54%)—
   继续观察,n≥20 前不下结论。

> ⚠️ Read with care: signal nights overlap in time (same market regime),
> symbols within a night are cross-correlated, and n is tiny — this is
> plumbing for the eventual verdict, not the verdict.
