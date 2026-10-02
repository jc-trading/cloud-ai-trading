# Recommendation outcomes — per-night IC (Fama-MacBeth)

> Generated 2026-09-10T15:06:15+00:00 by `python -m app.modules.simledger.outcomes_ic`. Read-only over `recommendations` + `recommendation_outcomes`; run `outcomes.py` first to refresh the forward returns.

- recommendations in DB: 916; evaluated: 552 (rest pending: trade_date bar not stored yet)
- directions: {'up': 552}
- analysed (direction=up): 552; era cuts on trade_date: ['2026-09-09', '2026-09-11']; min recs per night: 10

## Pooled Spearman IC (baseline — the number this report replaces)

- ret_1d: IC = +0.007 (n=552)
- ret_3d: IC = +0.025 (n=423)
- ret_5d: IC = +0.111 (n=365)

Pooled ranks every rec of every night in one basket, so it mostly measures which nights had good returns. The per-night numbers below are the ones to read.

## Per-night IC — Fama-MacBeth

### global — `all nights`

- recs: 552; nights: 6

| trade_date | n | with_1d | IC_1d | with_3d | IC_3d | with_5d | IC_5d |
|---|---|---|---|---|---|---|---|
| 2026-07-31 | 256 | 256 | +0.120 | 255 | +0.043 | 255 | +0.124 |
| 2026-08-04 | 52 | 52 | +0.035 | 52 | +0.181 | 52 | +0.139 |
| 2026-08-06 | 58 | 58 | +0.309 | 58 | +0.127 | 58 | +0.013 |
| 2026-08-07 | 58 | 58 | -0.150 | 58 | -0.281 | 0 | n/a |
| 2026-08-11 | 63 | 63 | -0.260 | 0 | n/a | 0 | n/a |
| 2026-08-12 | 65 | 65 | -0.266 | 0 | n/a | 0 | n/a |

| horizon | nights used | dropped | pending | FM mean IC | SE | t | NW-SE (lag) | t_NW |
|---|---|---|---|---|---|---|---|---|
| ret_1d | 6 | 0 | 0 | -0.035 | 0.094 | -0.38 | 0.094 (lag 0) | -0.38 |
| ret_3d | 4 | 0 | 2 | +0.018 | 0.103 | +0.17 | n/a | n/a |
| ret_5d | 3 | 0 | 3 | +0.092 | 0.040 | +2.31 | n/a | n/a |

### pre — `trade_date < 2026-09-09`

- recs: 552; nights: 6

| trade_date | n | with_1d | IC_1d | with_3d | IC_3d | with_5d | IC_5d |
|---|---|---|---|---|---|---|---|
| 2026-07-31 | 256 | 256 | +0.120 | 255 | +0.043 | 255 | +0.124 |
| 2026-08-04 | 52 | 52 | +0.035 | 52 | +0.181 | 52 | +0.139 |
| 2026-08-06 | 58 | 58 | +0.309 | 58 | +0.127 | 58 | +0.013 |
| 2026-08-07 | 58 | 58 | -0.150 | 58 | -0.281 | 0 | n/a |
| 2026-08-11 | 63 | 63 | -0.260 | 0 | n/a | 0 | n/a |
| 2026-08-12 | 65 | 65 | -0.266 | 0 | n/a | 0 | n/a |

| horizon | nights used | dropped | pending | FM mean IC | SE | t | NW-SE (lag) | t_NW |
|---|---|---|---|---|---|---|---|---|
| ret_1d | 6 | 0 | 0 | -0.035 | 0.094 | -0.38 | 0.094 (lag 0) | -0.38 |
| ret_3d | 4 | 0 | 2 | +0.018 | 0.103 | +0.17 | n/a | n/a |
| ret_5d | 3 | 0 | 3 | +0.092 | 0.040 | +2.31 | n/a | n/a |

### phase_a — `2026-09-09 <= trade_date < 2026-09-11`

no evaluable nights yet

### phase_c — `trade_date >= 2026-09-11`

no evaluable nights yet

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
bucket                                             
below_cut +0.0098   536 +0.0188   413 +0.0263   358
shortlist +0.0031    16 +0.0024    10 +0.0043     7
```

> ⚠️ Read with care: signal nights overlap in time (same market regime), symbols within a night are cross-correlated, and n is tiny — this is plumbing for the eventual verdict, not the verdict.
> The naive FM t is optimistic: it assumes the nightly ICs are independent, which 3d/5d overlapping windows are not — read t_NW instead wherever it exists. NW-SE shows n/a when fewer than 2*(lag+1) nights are available; that is not a pass for the naive t, it means neither SE is trustworthy yet.

