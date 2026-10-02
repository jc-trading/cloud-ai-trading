# Recommendation outcomes — per-night IC (Fama-MacBeth)

> Generated 2026-09-11T02:04:22+00:00 by `python -m app.modules.simledger.outcomes_ic`. Read-only over `recommendations` + `recommendation_outcomes`; run `outcomes.py` first to refresh the forward returns.

- recommendations in DB: 947; evaluated: 916 (rest pending: trade_date bar not stored yet)
- directions: {'up': 916}
- analysed (direction=up): 916; era cuts on trade_date: ['2026-09-09', '2026-09-11']; min recs per night: 10

## Pooled Spearman IC (baseline — the number this report replaces)

- ret_1d: IC = -0.016 (n=878)
- ret_3d: IC = -0.082 (n=833)
- ret_5d: IC = -0.090 (n=833)

Pooled ranks every rec of every night in one basket, so it mostly measures which nights had good returns. The per-night numbers below are the ones to read.

## Per-night IC — Fama-MacBeth

### global — `all nights`

- recs: 916; nights: 12

| trade_date | n | with_1d | IC_1d | with_3d | IC_3d | with_5d | IC_5d |
|---|---|---|---|---|---|---|---|
| 2026-07-31 | 256 | 256 | +0.120 | 255 | +0.043 | 255 | +0.123 |
| 2026-08-04 | 52 | 52 | +0.037 | 52 | +0.188 | 52 | +0.142 |
| 2026-08-06 | 58 | 58 | +0.327 | 58 | +0.129 | 58 | +0.005 |
| 2026-08-07 | 58 | 58 | -0.149 | 58 | -0.283 | 58 | -0.303 |
| 2026-08-11 | 63 | 63 | -0.260 | 63 | -0.170 | 63 | -0.478 |
| 2026-08-12 | 65 | 65 | -0.266 | 65 | -0.274 | 65 | -0.216 |
| 2026-08-14 | 72 | 72 | -0.053 | 72 | -0.150 | 72 | -0.159 |
| 2026-08-17 | 73 | 73 | -0.363 | 73 | -0.214 | 73 | -0.273 |
| 2026-08-18 | 71 | 71 | -0.156 | 71 | -0.128 | 71 | -0.166 |
| 2026-08-19 | 66 | 66 | -0.072 | 66 | -0.315 | 66 | -0.186 |
| 2026-09-09 | 44 | 44 | +0.094 | 0 | n/a | 0 | n/a |
| 2026-09-10 | 38 | 0 | n/a | 0 | n/a | 0 | n/a |

| horizon | nights used | dropped | pending | FM mean IC | SE | t | NW-SE (lag) | t_NW |
|---|---|---|---|---|---|---|---|---|
| ret_1d | 11 | 0 | 1 | -0.067 | 0.061 | -1.11 | 0.061 (lag 0) | -1.11 |
| ret_3d | 10 | 0 | 2 | -0.117 | 0.056 | -2.09 | 0.071 (lag 2) | -1.65 |
| ret_5d | 10 | 0 | 2 | -0.151 | 0.061 | -2.47 | 0.072 (lag 4) | -2.09 |

### pre — `trade_date < 2026-09-09`

- recs: 834; nights: 10

| trade_date | n | with_1d | IC_1d | with_3d | IC_3d | with_5d | IC_5d |
|---|---|---|---|---|---|---|---|
| 2026-07-31 | 256 | 256 | +0.120 | 255 | +0.043 | 255 | +0.123 |
| 2026-08-04 | 52 | 52 | +0.037 | 52 | +0.188 | 52 | +0.142 |
| 2026-08-06 | 58 | 58 | +0.327 | 58 | +0.129 | 58 | +0.005 |
| 2026-08-07 | 58 | 58 | -0.149 | 58 | -0.283 | 58 | -0.303 |
| 2026-08-11 | 63 | 63 | -0.260 | 63 | -0.170 | 63 | -0.478 |
| 2026-08-12 | 65 | 65 | -0.266 | 65 | -0.274 | 65 | -0.216 |
| 2026-08-14 | 72 | 72 | -0.053 | 72 | -0.150 | 72 | -0.159 |
| 2026-08-17 | 73 | 73 | -0.363 | 73 | -0.214 | 73 | -0.273 |
| 2026-08-18 | 71 | 71 | -0.156 | 71 | -0.128 | 71 | -0.166 |
| 2026-08-19 | 66 | 66 | -0.072 | 66 | -0.315 | 66 | -0.186 |

| horizon | nights used | dropped | pending | FM mean IC | SE | t | NW-SE (lag) | t_NW |
|---|---|---|---|---|---|---|---|---|
| ret_1d | 10 | 0 | 0 | -0.083 | 0.065 | -1.29 | 0.065 (lag 0) | -1.29 |
| ret_3d | 10 | 0 | 0 | -0.117 | 0.056 | -2.09 | 0.071 (lag 2) | -1.65 |
| ret_5d | 10 | 0 | 0 | -0.151 | 0.061 | -2.47 | 0.072 (lag 4) | -2.09 |

### phase_a — `2026-09-09 <= trade_date < 2026-09-11`

- recs: 82; nights: 2

| trade_date | n | with_1d | IC_1d | with_3d | IC_3d | with_5d | IC_5d |
|---|---|---|---|---|---|---|---|
| 2026-09-09 | 44 | 44 | +0.094 | 0 | n/a | 0 | n/a |
| 2026-09-10 | 38 | 0 | n/a | 0 | n/a | 0 | n/a |

| horizon | nights used | dropped | pending | FM mean IC | SE | t | NW-SE (lag) | t_NW |
|---|---|---|---|---|---|---|---|---|
| ret_1d | 1 | 0 | 1 | +0.094 | n/a | n/a | n/a | n/a |
| ret_3d | 0 | 0 | 2 | n/a | n/a | n/a | n/a | n/a |
| ret_5d | 0 | 0 | 2 | n/a | n/a | n/a | n/a | n/a |

### phase_c — `trade_date >= 2026-09-11`

no evaluable nights yet

## Mean forward return by confidence quintile (direction=up)

```
            n  conf_min  conf_max  ret_1d  ret_3d  ret_5d
quintile                                                 
1         176  +10.3540  +48.6360 +0.0016 +0.0087 +0.0123
2         175  +48.6490  +63.8480 +0.0048 +0.0170 +0.0238
3         176  +63.8780  +73.0160 +0.0093 +0.0194 +0.0272
4         175  +73.0500  +81.3970 +0.0058 +0.0095 +0.0110
5         176  +81.4390  +95.7720 -0.0001 +0.0037 +0.0059
```

## Shortlist vs below-the-cut (direction=up, mean returns)

```
           ret_1d        ret_3d        ret_5d      
             mean count    mean count    mean count
bucket                                             
below_cut +0.0046   840 +0.0119   805 +0.0162   805
shortlist -0.0032    38 +0.0019    28 +0.0087    28
```

> ⚠️ Read with care: signal nights overlap in time (same market regime), symbols within a night are cross-correlated, and n is tiny — this is plumbing for the eventual verdict, not the verdict.
> The naive FM t is optimistic: it assumes the nightly ICs are independent, which 3d/5d overlapping windows are not — read t_NW instead wherever it exists. NW-SE shows n/a when fewer than 2*(lag+1) nights are available; that is not a pass for the naive t, it means neither SE is trustworthy yet.

