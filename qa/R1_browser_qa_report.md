# R1 Browser QA Report — Cloud AI Trading (v3, stocks-only, simulation-only)

- Date: 2026-07-30 · Tool: gstack browse (headless Chromium) against http://localhost:3000 (Vite dev server, FastAPI at /api)
- Accounts: registered fresh `qa.browser2@example.com`; main run as `qa.browser@example.com`
- Method: real browser interaction per page; console log + network log captured per page; MutationObserver used to catch transient toasts
- Report-only. No files modified. Test data self-cleaned (fake Binance connection deleted via UI Disconnect, watchlist rows removed, sim position sold).

## Findings table

| # | Page/Flow | What was tested | Result | Severity | Evidence |
|---|-----------|-----------------|--------|----------|----------|
| 1 | Auth /register | Register new email qa.browser2@example.com | PASS | – | POST /api/v1/auth/register → 201, auto-login, lands on / |
| 2 | Auth logout | Profile dropdown → Sign Out | PASS | – | Redirects to /login, access/refresh tokens cleared from localStorage |
| 3 | Auth /login wrong password | Submit qa.browser@example.com + wrong password | **FAIL** | **major** | POST /api/v1/auth/login → 401 `{}` but **no error message ever renders** (checked at 0.6s/1s/2s/3s; MutationObserver saw zero DOM additions). Network shows /login page + auth.js re-fetched after the 401 → page appears to reload, wiping any feedback. Hypothesis: form submit not fully prevented on the error path |
| 4 | Auth /login correct + persistence | Login, then hard reload | PASS | – | Lands on /; after reload stays logged in on / (tokens in localStorage) |
| 5 | Auth guard | Direct URL to / while logged out | PASS | – | Redirects to /login?redirect=/ |
| 6 | / Recommendations | OOS badge strip | PASS | – | Renders: PF **1.34**, win rate 46%, avg R +0.16, source "R0-9 walk-forward OOS aggregate 2016-2026 (269 trades)" |
| 7 | / Recommendations | Shortlist cards | PASS | – | #1 **VZ** (uptrend now, 94, reason "close 47.22 above rising MA20 (43.80), MA5 on top") and #2 **LVS** (93) — phase badge + reason + "deterministic · no llm" |
| 8 | / Recommendations | Below-the-cut table + perf | PASS | – | **259 rows** render; page total load 33ms, scroll instant (11.5k px page). No jank, no console errors |
| 9 | / Recommendations | Card & row click navigation | PASS | – | VZ card → /market/VZ; CPRT table row → /market/CPRT |
| 10 | / Recommendations | Refresh button | PASS | – | Refetches GET /api/v1/sim/recommendations → 200 |
| 11 | Routing | /recommendations redirect | PASS | – | 302-style client redirect to / |
| 12 | Routing (deleted routes) | /portfolio /trading /signals /analysis /strategies /simulate | **FAIL** | **major** | **Blank white page** — no catch-all route: #app is 58 bytes, sidebar/layout not rendered, only console warning `[Vue Router warn]: No match found for location with path "/portfolio"` (same for all six). User is stranded with no UI |
| 13 | /sim panels | Both accounts render | PASS | – | "My practice account" + "对照 · system account" both $2,000.00 cash, 0 lots, equity-curve placeholder text |
| 14 | /sim BUY | AAPL qty 0.05 stop 200 | PARTIAL | major | POST /api/v1/sim/trade → 200; cash $2000 → $1983.38, AAPL 0.05 @ 332.50 row appears — **but no success toast and the panel does NOT auto-refresh** (numbers only update after clicking Refresh) |
| 15 | /sim SELL | Sell the AAPL position | PARTIAL | major | POST /sim/trade → 200; position gone, cash $1999.98 (≈$2,000 minus costs) — same issue: no toast, manual Refresh required |
| 16 | /sim error: stop above price | AAPL stop 400 (price ~332) | **FAIL** | **major** | POST /sim/trade → 422 `{"detail":"a buy requires stop < current price"}` — **zero UI feedback** (MutationObserver captured no DOM change, twice). Form silently keeps its values |
| 17 | /sim error: missing fields | Submit with empty Symbol | PASS | – | Native HTML5 validation blocks submit: "Please fill out this field." No request fired |
| 18 | /sim error: absurd symbol | BUY ZZZZZZ | **FAIL** | **major** | POST /sim/trade → 422 `{"detail":"no live quote for ZZZZZZ"}` — again **completely silent** in the UI. (Note: a `jd-toast` component exists and works on /settings/exchange, so the sim page just never wires errors to it) |
| 19 | /market default tab | Page load | PARTIAL | major | Page loads, but the **default tab is 🔶 Crypto** with 12 crypto pairs served by GET /api/v1/market/tickers → 200 (2.3KB) — crypto backend "deleted" yet tickers still served. Data anomaly: MATIC/USDT shows −18.60% with Day High/Low **$0.000000 / $0.000000**, volume 0 |
| 20 | /market copy mismatch | Topbar vs page subtitle | PARTIAL | cosmetic | Topbar: "Live US stock prices" (stocks-only v3); page header below: "Real-time crypto and US stock prices" |
| 21 | /market US Stocks tab | Tab switch + data | PASS | minor | 12 stocks render (AAPL…PLTR). GET /api/v1/market/tickers/stocks took **4.9s** (visible "Loading..." state) |
| 22 | /market search | Search NVDA | PASS | – | Client-side filter → table shows only NVDA, "Showing 1–1 of 1" |
| 23 | /market crypto row click | BTC/USDT row | PARTIAL | minor | Navigates to /market/BTC%2FUSDT; ticker data renders (price $64,580) but candles GET → 200 `[]` after **10.9s**; page correctly shows "No candle data available for this timeframe." |
| 24 | /market/:symbol chart | VZ + NVDA detail | **FAIL** | **major** | Price/24h/High-Low/Volume render, but the **Price Chart is a dead widget**: `.chart-container` is completely empty (0 canvas, 0 svg elements) even though /api/v1/market/VZ/candles returns full OHLCV data. No console error, no fallback message (unlike the crypto "no data" case) |
| 25 | /market/:symbol intervals | 1m/5m/15m/1H/4H/1D tabs | PARTIAL | minor | Tabs fire candle requests (interval=1d etc.) but nothing can render (see #24). 1D returns only 1 candle for limit=200 |
| 26 | /market/:symbol buttons | Trade, "Trade →", Set Alert | **FAIL** | major | All three are **inert**: no modal, no navigation, no toast, no request, no console error (likely pointed at the deleted /trading route) |
| 27 | /market/:symbol watchlist | Add to Watchlist toggle | PASS | – | POST /watchlists/default/items → 201; button flips to "In Watchlist"/"Remove from Watchlist" |
| 28 | /watchlist add | Add Symbol dialog + autocomplete + add NVDA | PASS | minor | Autocomplete returns results; "+" adds → 201; row renders with live price ($194.43, +2.33%, high/low, date). Stock search slow: GET /market/search/stocks?q=NVDA → **4.1s** |
| 29 | /watchlist crypto leakage | Autocomplete sources | PARTIAL | major | Search hits **both** /market/search/stocks and /market/search/crypto → crypto returns 9+ tokenized-NVDA crypto results (NVDAON, NVDAX, bNVDA…) offered for adding, on a stocks-only platform. Empty state also says "track crypto or US stocks" |
| 30 | /watchlist remove | Remove NVDA | PASS | – | DELETE /watchlists/default/items/<id> → 204; list count 1 → 0; auto-updates (no confirm dialog) |
| 31 | /settings | Which controls are live vs static | PARTIAL (documented, known-static) | major | Entire page is **inert**: only API call is /auth/me. Email field hardcoded to "**user@example.com**" (not the logged-in qa.browser@example.com), Full Name empty. All 6 buttons — Save Changes, Save Preferences, Enable 2FA, Manage API Keys, View All Sessions, Update Password — fire **zero** requests and no navigation ("Manage API Keys" doesn't even link to /settings/exchange). Timezone list differs from Register's list (US-centric vs Asia-centric) |
| 32 | /settings/exchange render | Page + list | PASS | – | Renders; GET /api/v1/exchanges → 200 `[]`; cards: Binance, Alpaca (paper/live), Bitget + OKX "Coming Soon"; AES-256 security note |
| 33 | /settings/exchange Binance | Binance still offered + fake-key Test Connection | **FAIL** | **blocker→major** | Binance card fully active (key/secret/mode/Connect/Test). With throwaway fake keys, "Test Connection" **first persists the connection** (POST /api/v1/exchanges → **201**, record saved with exchange_type "binance", is_active true) and then POST /exchanges/<id>/test → **500** bare "Internal Server Error"; toast shows raw "Test Failed — Request failed with status code 500". So the backend does NOT refuse Binance at creation (201) but crashes on test — UI/backend mismatch both ways, and untested keys are stored as active connections |
| 34 | /settings/exchange Disconnect | Remove the fake connection | PASS | minor | DELETE /api/v1/exchanges/<id> → 204, card returns to Disconnected. No confirmation dialog before disconnect |
| 35 | Global sidebar | Exact items + navigation | PASS | – | Exactly: Recommendations, Sim Accounts, Market Overview, Watchlist, Exchanges, Settings (grouped Overview/Market Data/Account). All 6 navigate correctly |
| 36 | Global topbar titles | Title/description per page | PASS | cosmetic | Match each page (Recommendations / Sim Accounts / Market Overview / Market Detail / Watchlist / Settings / Exchange Connections) — except the /market subtitle mismatch (#20) |
| 37 | Global notifications bell | Click bell | PARTIAL (decorative, as suspected) | minor | Click produces nothing: no dropdown, no request, no DOM change |
| 38 | Global profile dropdown | Items + links | PASS | – | Profile → /settings, Settings → /settings, API Keys → /settings/exchange, Sign Out works |
| 39 | Per-page console/network | All pages visited | PASS | – | No console errors and no failed XHRs on any page except: Vue Router warns on deleted routes (#12) and the intentional 401/422/500s triggered by tests |

## Prioritized failures

1. **Sim trade errors are 100% silent** (#16, #18) — 422 from /sim/trade (stop-above-price, unknown symbol) produces no toast/inline error; user can't tell why nothing happened. Toast component exists elsewhere (jd-toast), just not wired here.
2. **Wrong-password login gives no feedback** (#3) — 401 swallowed; login page appears to reload and sits there blank-faced.
3. **SymbolDetail price chart is dead** (#24) — empty `.chart-container`, no chart lib output at all despite candles data; likely orphaned since legacy market_data/chart lib removal.
4. **Binance UI/backend mismatch + fake keys persisted** (#33) — Binance offered in UI; POST /exchanges accepts binance (201) and stores the keys, then /test 500s with a raw error. Backend "refusal" is actually an unhandled 500 after persistence.
5. **Deleted routes render a blank page** (#12) — no router catch-all; six old URLs give a white screen with no layout.
6. **Sim panel doesn't auto-refresh after trades** (#14, #15) — success also gives no toast; state only updates on manual Refresh.
7. **Crypto not actually gone** (#19, #29) — /market defaults to a live Crypto tab (tickers endpoint still serving), watchlist autocomplete still queries and offers crypto tokens; MATIC row shows corrupt $0.000000 high/low.
8. **Dead Trade / Trade → / Set Alert buttons on SymbolDetail** (#26).
9. Perf notes: /market/tickers/stocks 4.9s, stock search 4.1s, crypto candles 10.9s (all with loading states, but slow).

---

## Fix round + re-verification (2026-07-30, same day)

All 9 priority failures fixed and independently re-verified in a fresh browser
pass — 10/10 PASS, zero new console errors. Root causes worth recording:
- useToast() lacked .success/.error methods → TypeError inside try blocks ate
  both toasts AND the post-trade refresh (findings #7/#8)
- the global axios 401 interceptor routed login failures into the token-refresh
  → window.location redirect ate the error message (finding #2)
- lightweight-charts v5 removed addCandlestickSeries + a v-if race meant the
  chart container wasn't mounted when renderChart ran (finding #10)
- crypto surfaces (Market tab, watchlist autocomplete, crypto endpoints,
  Binance/Bitget/OKX cards) stripped: stocks-only end to end
- deleted routes now redirect to /; Settings shows the real user with no dead
  controls; notifications bell removed; exchange create rejects non-Alpaca 422
  and test failures return clean errors

Known items deliberately left (non-blocking):
1. stocks tickers endpoint slow (~5-10s first load; serial Alpaca candle pulls)
2. watchlist autocomplete race: stale slow responses can overwrite newer results
3. exchange "Test Connection" persists the connection before testing (state
   reconciles on reload)
4. legacy watchlist rows created pre-v3 may still carry market_type=crypto
   (display-only fallback badge)
QA account deleted after the fix round (its password had been committed to git — credential neutralized by account deletion; see code-review candidate #13).
