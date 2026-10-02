# Recommendation outcomes — per-night IC (Fama-MacBeth)

> Generated 2026-09-21T04:46:05+00:00 by `python -m app.modules.simledger.outcomes_ic`. Read-only over `recommendations` + `recommendation_outcomes`; run `outcomes.py` first to refresh the forward returns.

- recommendations in DB: 1098; evaluated: 1098 (rest pending: trade_date bar not stored yet)
- directions: {'up': 1098}
- analysed (direction=up): 1098; era cuts on trade_date: ['2026-09-09', '2026-09-11']; min recs per night: 10

## Pooled Spearman IC (baseline — the number this report replaces)

- ret_1d: IC = +0.036 (n=1066)
- ret_3d: IC = -0.017 (n=1010)
- ret_5d: IC = -0.037 (n=946)

Pooled ranks every rec of every night in one basket, so it mostly measures which nights had good returns. The per-night numbers below are the ones to read.

## Per-night IC — Fama-MacBeth

### global — `all nights`

- recs: 1098; nights: 18

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
| 2026-09-09 | 44 | 44 | +0.094 | 44 | -0.040 | 44 | +0.058 |
| 2026-09-10 | 38 | 38 | -0.010 | 38 | -0.078 | 38 | +0.156 |
| 2026-09-11 | 31 | 31 | -0.306 | 31 | +0.001 | 31 | +0.256 |
| 2026-09-14 | 35 | 35 | +0.298 | 35 | +0.552 | 0 | n/a |
| 2026-09-15 | 29 | 29 | +0.419 | 29 | +0.343 | 0 | n/a |
| 2026-09-16 | 28 | 28 | +0.472 | 0 | n/a | 0 | n/a |
| 2026-09-17 | 27 | 27 | +0.288 | 0 | n/a | 0 | n/a |
| 2026-09-18 | 32 | 0 | n/a | 0 | n/a | 0 | n/a |

| horizon | nights used | dropped | pending | FM mean IC | SE | t | NW-SE (lag) | t_NW |
|---|---|---|---|---|---|---|---|---|
| ret_1d | 17 | 0 | 1 | +0.025 | 0.064 | +0.39 | 0.064 (lag 0) | +0.39 |
| ret_3d | 15 | 0 | 3 | -0.026 | 0.063 | -0.42 | 0.084 (lag 2) | -0.31 |
| ret_5d | 13 | 0 | 5 | -0.080 | 0.061 | -1.32 | 0.082 (lag 4) | -0.98 |

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
| 2026-09-09 | 44 | 44 | +0.094 | 44 | -0.040 | 44 | +0.058 |
| 2026-09-10 | 38 | 38 | -0.010 | 38 | -0.078 | 38 | +0.156 |

| horizon | nights used | dropped | pending | FM mean IC | SE | t | NW-SE (lag) | t_NW |
|---|---|---|---|---|---|---|---|---|
| ret_1d | 2 | 0 | 0 | +0.042 | 0.052 | +0.82 | 0.052 (lag 0) | +0.82 |
| ret_3d | 2 | 0 | 0 | -0.059 | 0.019 | -3.15 | n/a | n/a |
| ret_5d | 2 | 0 | 0 | +0.107 | 0.049 | +2.19 | n/a | n/a |

### phase_c — `trade_date >= 2026-09-11`

- recs: 182; nights: 6

| trade_date | n | with_1d | IC_1d | with_3d | IC_3d | with_5d | IC_5d |
|---|---|---|---|---|---|---|---|
| 2026-09-11 | 31 | 31 | -0.306 | 31 | +0.001 | 31 | +0.256 |
| 2026-09-14 | 35 | 35 | +0.298 | 35 | +0.552 | 0 | n/a |
| 2026-09-15 | 29 | 29 | +0.419 | 29 | +0.343 | 0 | n/a |
| 2026-09-16 | 28 | 28 | +0.472 | 0 | n/a | 0 | n/a |
| 2026-09-17 | 27 | 27 | +0.288 | 0 | n/a | 0 | n/a |
| 2026-09-18 | 32 | 0 | n/a | 0 | n/a | 0 | n/a |

| horizon | nights used | dropped | pending | FM mean IC | SE | t | NW-SE (lag) | t_NW |
|---|---|---|---|---|---|---|---|---|
| ret_1d | 5 | 0 | 1 | +0.234 | 0.140 | +1.68 | 0.140 (lag 0) | +1.68 |
| ret_3d | 3 | 0 | 3 | +0.299 | 0.161 | +1.86 | n/a | n/a |
| ret_5d | 1 | 0 | 5 | +0.256 | n/a | n/a | n/a | n/a |

## Mean forward return by confidence quintile (direction=up)

```
            n  conf_min  conf_max  ret_1d  ret_3d  ret_5d
quintile                                                 
1         214  +10.3540  +46.1300 -0.0009 +0.0021 +0.0068
2         213  +46.1630  +62.4820 +0.0019 +0.0157 +0.0210
3         213  +62.5420  +71.8750 +0.0065 +0.0089 +0.0176
4         213  +71.9880  +80.5030 +0.0062 +0.0135 +0.0147
5         213  +80.5900  +95.7720 +0.0021 +0.0050 +0.0059
```

## Shortlist vs below-the-cut (direction=up, mean returns)

```
           ret_1d        ret_3d        ret_5d      
             mean count    mean count    mean count
bucket                                             
below_cut +0.0035   973 +0.0098   933 +0.0138   888
shortlist -0.0009    93 -0.0001    77 +0.0055    58
```

> ⚠️ Read with care: signal nights overlap in time (same market regime), symbols within a night are cross-correlated, and n is tiny — this is plumbing for the eventual verdict, not the verdict.
> The naive FM t is optimistic: it assumes the nightly ICs are independent, which 3d/5d overlapping windows are not — read t_NW instead wherever it exists. NW-SE shows n/a when fewer than 2*(lag+1) nights are available; that is not a pass for the naive t, it means neither SE is trustworthy yet.

