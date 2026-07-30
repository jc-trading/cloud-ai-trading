# /code-review 7440a71..HEAD — IN PROGRESS (paused 2026-07-30, usage reset)

Protocol: 8 finder angles → dedup → 1-vote verify → ≤10 findings. Writer≠reviewer.
**Status: 2/7 finders returned (12 candidates below, UNVERIFIED). 5 finders
(A line-scan / B removed-behavior / C cross-file / reuse+simplification /
conventions) died with the session — re-run them, then dedup + verify ALL.**

## Efficiency angle (6 candidates)
1. quant_tasks.py:200 — signal_cycle serial per-symbol sync_daily (~500 HTTP round-trips,
   2-4 min) though batched fetch_daily_multi() exists (fetch.py:105); sync_daily also
   double-reads the parquet per symbol just to count rows.
2. cycles.py:173 — daily_exit_management re-reads bars + re-runs compute_signals for
   symbols build_recommendations already processed same cycle → pass a memoized bars_fn.
3. cycles.py:241 — run_entries: serial BLOCKING FinnhubClient.quote inside async fn
   (blocks event loop), held+shortlist quoted twice → gather via asyncio.to_thread
   (pattern exists at market/service.py:24-40).
4. cycles.py:350 — check_stops: same serial blocking quote per position, 78 cycles/day.
5. quant_tasks.py:226 — snapshot block: serial quotes + snapshot() re-queries
   get_open_positions already in hand → optional positions/equity params.
6. service.py:95 — open_or_add N+1 (already_booked + get_open_position SELECT per order)
   → batch idempotency prefetch / pass known position.

## Altitude angle (6 candidates)
7. frontend/src/api/client.js:35 — 401 exemption is a hardcoded /auth/(login|register|refresh)
   regex → future auth endpoints re-inherit the eaten-error bug; use per-request flag.
8. celery_app.py:111 + quant_tasks.py:150 — entry DST = 2 crontab slots + hand-rolled
   40-min window in another file; constants must silently agree → tz-aware schedule.
9. quant_tasks.py:80 — _system_account (business rule) lives in a tasks module, imported
   by router + telegram → belongs on SimLedgerService.system_account(db).
10. simledger/router.py:31 — OOS_BADGE hardcoded → goes silently stale when R0-9 reruns;
    read from promoted results.json / DB row (data source already exists).
11. cycles.py:54 — HALT_SENTINEL="/app/runtime/HALT" container-only path; /kill writer
    uses it directly → PermissionError on host-run = the kill switch itself fails.
    → settings-driven path.
12. migrations/013:31 — downgrade() raises NotImplementedError, breaks repo convention
    (006 uses documented pass stub); blocks any downgrade walk past 013.

Rejected by the altitude finder (recorded so it isn't re-litigated): frontend
catch-all redirect = deliberate product choice, not a bandaid.

## Resume
1. Re-run finder angles A/B/C + reuse/simplification + conventions on 7440a71..HEAD.
2. Dedup all candidates (12 above + new) → verify each (CONFIRMED/PLAUSIBLE/REFUTED,
   plausible-by-default) → ≤10 findings ranked → then fix round.
