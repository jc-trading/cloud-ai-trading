# Recommendation outcomes — forward returns & confidence IC

> Generated 2026-08-14 08:48 MYT by `python -m app.modules.simledger.outcomes`
> (run #3 — absorbed the 08-12 recs after the 05:30 signal_cycle synced their
> session bar). Idempotent; re-run any night to refresh. Pending 72 = the
> 08-14 recs, evaluable after the next session's bars land.

- recommendations in DB: 624; evaluated: 552 (rest pending: trade_date bar not stored yet)
- directions: {'up': 552}

## Coverage per trade_date

```
trade_date   n  with_1d  with_3d  with_5d
2026-07-31 256      256      255      255
2026-08-04  52       52       52       52
2026-08-06  58       58       58       58
2026-08-07  58       58       58        0
2026-08-11  63       63        0        0
2026-08-12  65       65        0        0
```

## Confidence IC (Spearman, direction=up, n=552)

- ret_1d: IC = +0.007 (n=552)
- ret_3d: IC = +0.025 (n=423)
- ret_5d: IC = +0.111 (n=365)

## Mean forward return by confidence quintile (direction=up)

```
            n  conf_min  conf_max  ret_1d  ret_3d  ret_5d
quintile
1         111  +13.8480  +48.9790 +0.0048 +0.0065 +0.0030
2         110  +49.0840  +65.0630 +0.0082 +0.0176 +0.0242
3         110  +65.2230  +74.0280 +0.0189 +0.0303 +0.0449
4         110  +74.0840  +82.3880 +0.0094 +0.0235 +0.0328
5         111  +82.4650  +95.7720 +0.0066 +0.0141 +0.0253
```

## Shortlist vs below-the-cut (direction=up, mean returns)

```
           ret_1d        ret_3d        ret_5d
             mean count    mean count    mean count
below_cut +0.0098   536 +0.0188   413 +0.0263   358
shortlist +0.0031    16 +0.0024    10 +0.0043     7
```

## Run #2 → #3 delta

| Metric | run #2 (08-12, n=486) | run #3 (08-14, n=552) |
|---|---|---|
| IC ret_1d | +0.104 | **+0.007** |
| IC ret_3d | +0.069 | +0.025 |
| IC ret_5d | +0.127 | +0.111 |

Adding one night (65 recs) moved pooled 1d IC by ~0.10. Pooled-across-nights
Spearman is dominated by between-night return level differences, not by
within-night ranking skill — the metric to trust is per-night IC averaged across
nights (Fama-MacBeth style), not this pooled number. Not yet implemented.

Stable across all three runs: the quintile curve is non-monotonic at the top
(Q3 best, Q5 falls back), and shortlist trails below-the-cut (n still ≤16).

> ⚠️ Read with care: signal nights overlap in time (same market regime), symbols
> within a night are cross-correlated, and n is tiny — this is plumbing for the
> eventual verdict, not the verdict.
