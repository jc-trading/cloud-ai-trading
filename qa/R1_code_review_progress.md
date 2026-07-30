# /code-review 7440a71..HEAD — IN PROGRESS (paused 2026-07-30, usage reset)

Protocol: 8 finder angles → dedup → 1-vote verify → ≤10 findings. Writer≠reviewer.
**Status: 5/7 finders returned (12 candidates below, UNVERIFIED). 2 finders
(A line-scan / C cross-file) still to re-run, then dedup + verify ALL.**

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

## Conventions angle (2 candidates — finder returned at pause time)
13. qa/R1_browser_qa_report.md — working QA password was committed (and pushed) in
    b0a0bbd. MITIGATED same day: QA accounts deleted from DB (credential dead),
    line stripped from the report. History still contains the string — acceptable
    because the account no longer exists; do NOT reuse that password.
14. CLAUDE.md (repo root) — still describes the deleted crypto system as “当前状态”;
    future sessions loading it get a map of a system that no longer exists.
    → rewrite for v3 stocks-only/simledger architecture in the fix round.
Clean per this finder: engine purity, RAW-adjust-on-read, no-live-trading, UI_RULES.

## Reuse + Simplification angle (6 candidates)
15. watchdog.py:130+163 — RTH/ET logic re-implemented (3rd copy vs quant_tasks._in_rth
    + entry window math) → half-day sessions will drift, false stop-monitoring alerts.
16. simledger/router.py:115 — /sim/trade hand-builds QuoteReading (dup of
    quant_tasks._quote_fn) → staleness semantics can diverge between manual/auto trades;
    move conversion to cycles.finnhub_quote().
17. telegram_tasks.py:50/81 + quant_tasks.py:159 + cycles.py:303 — SafetyState-by-scope
    lookup copy-pasted 4x with 2 divergent get-or-create variants → one
    cycles.get_safety_state(db, account, create=) owner of the scope-key convention.
18. telegram_tasks.py:139 — offset persistence re-implements _beat() upsert it already
    imports the module of → drift risk = replayed /kill//resume commands.
19. market/service.py:501+562 etc — ~250 lines dead crypto plane (search_crypto,
    search_symbols, get_tickers, ccxt branch, CoinGecko mapping) with zero reachable
    routes → delete; only legacy market_type=crypto watchlist rows touch it.
20. SimAccount.vue:190 — two scoped style blocks; ~10 rules duplicated verbatim vs
    Recommendations.vue (already drifted: .d-sym 19px vs 17px) → promote shared chrome
    to main.css.
Not flagged (recorded): zones.py is live; Position<->SimPosition converter below bar
at 1 call site; parked modules' _run_async variants not worth consolidating.

## Removed-behavior angle B (5 candidates)
21. watchdog.py:79 — _check_decision_freshness monitors the DELETED crypto producer;
    legacy active QuantStrategy row + watchlist items = false 'stale decisions' alert
    every 6h forever (alert-fatigue risk); on clean DB silently dead. → delete or
    re-point at Recommendation freshness.
22. exchange/service.py:27 + router balance route — legacy binance ExchangeConnection
    row + GET /exchanges/{id}/balance → unhandled ValueError → 500 (test route got the
    422 shield, balance didn't); POST still accepts exchange_type=binance via enum.
    → shield balance route, restrict schema enum, optionally purge legacy rows.
23. celery_health.py:27 — retired tasks' task_statuses rows never deleted → /api/v1/system
    health stuck critical-unhealthy forever; new quant.* tasks absent from EXPECTED_TASKS
    (zero health coverage of the v3 pipeline in that view).
24. frontend api/auth.js:11 — listUsers/updateUserRole orphaned (admin UI deleted, backend
    endpoints survive) → role management (incl. SUPER_ADMIN the 对照账户 depends on) has
    no UI path; decide: delete exports or restore a minimal admin surface.
25. celery_app.py:57 — stale comment claims risk_tasks is in the include list (deleted).
Clean per this finder: no surviving imports of deleted modules; beat entries all live;
frontend calls all map to mounted routes; migration 013 tables have no surviving FK/ORM refs.

## Resume
1. Re-run finder angles A/B/C only on 7440a71..HEAD (others archived above).
2. Dedup all candidates (12 above + new) → verify each (CONFIRMED/PLAUSIBLE/REFUTED,
   plausible-by-default) → ≤10 findings ranked → then fix round.
