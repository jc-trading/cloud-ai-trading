#!/usr/bin/env bash
# 深夜股票检测 (Night Watch) — launcher for the 00:00–08:00 MYT paper-observation run.
#
# The G2 observation window on this Mac covers the US afternoon session
# (00:00–04:00 MYT = 12:00–16:00 ET) plus the nightly signal_cycle at
# 05:30 MYT (21:30 UTC). This script starts/stops that run and answers
# "跑起来了没 / 今晚有没有推荐 / 现在什么状态" without touching any trading logic.
#
#   ./scripts/night-watch.sh start    开跑: caffeinate + containers + 状态小结
#   ./scripts/night-watch.sh status   实况: 容器/队列/心跳/推荐/持仓/保护状态
#   ./scripts/night-watch.sh stop     收工: 停容器 + 释放 caffeinate
#   ./scripts/night-watch.sh logs     跟看 worker 日志 (Ctrl-C 退出)
#   ./scripts/night-watch.sh signal   手动补跑 signal_cycle (只在 ET 收盘后有意义 —
#                                     它发的是「下一个 session」的推荐)
#
# Full history is in the dashboard: 「Night Watch」 page (per-night log) and
# 「LLM Log」 page (AI usage/cost).

set -euo pipefail
cd "$(dirname "$0")/.."

DC="docker compose"
CAFFEINATE_PID_FILE="runtime/night-watch.caffeinate.pid"
# Host port 8000 is shadowed by a native php83 server on IPv4 (2026-07-06 note):
# always probe health INSIDE the backend container, never via localhost:8000.
# The backend image has no curl — probe with its python instead. A function,
# not a $VAR (unquoted expansion would shred the quoted -c argument).
health_ok() {
  $DC exec -T backend python -c \
    "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health', timeout=3)" \
    >/dev/null 2>&1
}

sql() {  # run one read-only query against the app DB (creds from container env)
  $DC exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -X -A -t -F " | "' <<< "$1"
}

start_caffeinate() {
  mkdir -p runtime
  if [[ -f "$CAFFEINATE_PID_FILE" ]] && kill -0 "$(cat "$CAFFEINATE_PID_FILE")" 2>/dev/null; then
    echo "· caffeinate already running (pid $(cat "$CAFFEINATE_PID_FILE"))"
    return
  fi
  nohup caffeinate -is >/dev/null 2>&1 &
  echo $! > "$CAFFEINATE_PID_FILE"
  echo "· caffeinate started (pid $!) — Mac 整夜不睡"
}

stop_caffeinate() {
  if [[ -f "$CAFFEINATE_PID_FILE" ]]; then
    kill "$(cat "$CAFFEINATE_PID_FILE")" 2>/dev/null || true
    rm -f "$CAFFEINATE_PID_FILE"
    echo "· caffeinate stopped"
  fi
}

wait_healthy() {
  echo -n "· waiting for backend health"
  for _ in $(seq 1 30); do
    if health_ok; then echo " — healthy"; return 0; fi
    echo -n "."
    sleep 2
  done
  echo " — FAILED (check: $DC logs backend | tail -50)"
  return 1
}

show_status() {
  echo "== 深夜股票检测 · status =="
  echo
  echo "-- containers --"
  $DC ps --format 'table {{.Service}}\t{{.Status}}' 2>/dev/null || $DC ps
  echo
  if ! health_ok; then
    echo "backend: NOT healthy — run '$0 start' first"
    return 1
  fi
  echo "-- queue (should be ~0) --"
  echo "celery queue depth: $($DC exec -T redis redis-cli -n 1 llen celery)"
  echo
  echo "-- heartbeats (cycle | last beat UTC | meta) --"
  sql "SELECT name, to_char(last_beat_at, 'MM-DD HH24:MI:SS'), coalesce(meta::text,'')
       FROM heartbeats ORDER BY name;"
  echo
  echo "-- recommendations (last 3 sessions) --"
  sql "SELECT trade_date, count(*),
              count(*) FILTER (WHERE shortlist_rank IS NOT NULL) AS shortlist,
              count(llm_explanation) AS explained
       FROM recommendations GROUP BY trade_date ORDER BY trade_date DESC LIMIT 3;"
  echo
  echo "-- latest shortlist --"
  sql "SELECT trade_date, shortlist_rank, symbol, confidence, phase
       FROM recommendations
       WHERE shortlist_rank IS NOT NULL
         AND trade_date = (SELECT max(trade_date) FROM recommendations)
       ORDER BY shortlist_rank;"
  echo
  echo "-- system account (对照账户) --"
  sql "SELECT name, cash, updated_at::date FROM sim_accounts WHERE is_system;"
  sql "SELECT 'open: ' || symbol, shares, avg_cost, stop
       FROM sim_positions p JOIN sim_accounts a ON a.id = p.account_id
       WHERE a.is_system AND p.status = 'open';"
  echo
  echo "-- protections --"
  sql "SELECT scope, halted, coalesce(halted_until::text,'-'),
              coalesce(paused_until::text,'-'), coalesce(reason,'')
       FROM safety_state;"
  echo
  echo "详情看 dashboard: 「Night Watch」(每晚记录) · 「LLM Log」(AI 花销)"
}

case "${1:-}" in
  start)
    echo "== 深夜股票检测 · start =="
    start_caffeinate
    $DC up -d
    wait_healthy
    echo
    show_status || true
    echo
    echo "提醒: 跑过 05:30 MYT (signal_cycle 出次日推荐) 再 stop。"
    ;;
  status)
    show_status
    ;;
  stop)
    echo "== 深夜股票检测 · stop =="
    stop_caffeinate            # first: even if docker is already down,
    $DC stop || true           # the Mac must not stay awake forever
    echo "· containers stopped (data volumes kept)"
    ;;
  logs)
    $DC logs -f --tail 100 celery-worker
    ;;
  signal)
    # Manual catch-up. signal_cycle anchors on "today ET" and publishes recs for
    # the NEXT session — useful only after ET close (>= ~04:20 MYT) when the
    # 05:30 beat was missed; mid-session it would just rebuild tomorrow's recs.
    echo "触发 quant.signal_cycle (发的是下一个 session 的推荐) ..."
    $DC exec -T celery-worker celery -A tasks.celery_app call quant.signal_cycle
    echo "已入队 — 跟进: $0 logs"
    ;;
  *)
    echo "usage: $0 {start|status|stop|logs|signal}"
    exit 1
    ;;
esac
