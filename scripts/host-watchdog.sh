#!/usr/bin/env bash
# 宿主侧独立探活 (Host Watchdog) — 补容器内 watchdog 救不了的那一类故障。
#
# 为什么需要它 (两次线上实证, 见 cloud-ai-trading-log.md):
#   2026-09-11  PG 锁文件写坏 → backend 卡在 "Waiting for application startup"
#               → 进程内 watchdog 随之死亡 → 停摆 8h12m, 零告警。
#   2026-09-18  容器内 DNS 全挂 → signal_cycle 507/507 bars 失败 fail-closed
#               → 告警走同一条断网, 一条没发出, 恢复后也没补发。
# 所以本脚本跑在宿主 (launchd), 不依赖 backend 进程、不依赖容器内网络;
# Telegram 发不出去时退回 macOS 本地通知 —— 网断了至少屏幕上看得见。
#
# ⚠ 它救不了的两类 (不要误以为已覆盖):
#   1. Mac 睡眠 —— launchd StartInterval 在睡眠时不触发, 醒来才补跑一次。
#   2. Mac 整机网络断 —— 外发告警本身出不去, 只剩本地通知。
#   这两类只有机器之外的 dead man's switch (外部服务检测「心跳消失」) 能覆盖。
#
#   ./scripts/host-watchdog.sh          跑一轮检查 (launchd 用这个)
#   ./scripts/host-watchdog.sh --dry    只打印判定, 不发告警
#   ./scripts/host-watchdog.sh --test   强制发一条测试告警, 验证送达路径

set -uo pipefail
cd "$(dirname "$0")/.."

DC="docker compose"
STATE_DIR="${CAT_WD_STATE_DIR:-runtime}"
STATE_FILE="$STATE_DIR/host-watchdog.state"
LOG_FILE="$STATE_DIR/host-watchdog.log"
COOLDOWN_SEC=3600          # 同一组问题 1 小时内不重复发
CONTAINERS="cat_postgres cat_redis cat_backend cat_celery_worker cat_celery_beat cat_market_stream"

mkdir -p "$STATE_DIR"

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$LOG_FILE"; }

# --- 有界执行: 每个外部调用都要有上限 (2026-10-01 VM 卡死, docker info 永不返回
#     → 本脚本挂了 8h, launchd 不会再起新实例 → 零告警) ----------------------
# 超时则 SIGKILL 整棵进程树并返回 124, 调用方据此区分「超时」与「失败」。
# 不用 GNU timeout (launchd 的 PATH 没有); 不用 perl alarm (docker CLI 是 Go, 未必死于 SIGALRM)。
# 杀整棵树: 先 STOP (冻住不能再 fork) 再自底向上 SIGKILL、根最后死 —— wait 返回时孙进程 (docker-compose 插件) 已死, 不会占着 $(...) 的管道。
kill_tree() {
  local kids k
  kill -STOP "$1" 2>/dev/null
  kids=$(pgrep -P "$1")
  for k in $kids; do kill_tree "$k"; done
  kill -9 "$1" 2>/dev/null
}

run_to() {
  local secs="$1" pid killer rc flag="$STATE_DIR/.run_to.$$"
  shift
  rm -f "$flag"
  "$@" <&0 &
  pid=$!
  ( sleep "$secs"; : > "$flag"; kill_tree "$pid" ) </dev/null >/dev/null 2>&1 &
  killer=$!
  wait "$pid"; rc=$?
  # 超时与否只看 killer 动手前落下的 flag: rc 不可靠 (命令自己也会 exit 137), SECONDS 是墙钟 (校时会跳)。
  # 先 STOP 冻住 killer: 有 flag → 已在杀树, CONT 让它杀完; 无 flag → 还没动手, 连同它的 sleep 一起杀掉。
  kill -STOP "$killer" 2>/dev/null
  if [[ -e "$flag" ]]; then
    kill -CONT "$killer" 2>/dev/null
    wait "$killer"
    rm -f "$flag"
    return 124
  fi
  kill -9 $(pgrep -P "$killer") "$killer" 2>/dev/null
  wait "$killer"
  return "$rc"
}

# --- 告警送达: 宿主 curl 优先, 失败退回 macOS 通知 -------------------------
notify() {
  local msg="$1" sent=0
  local token chat
  token=$(grep -E '^TELEGRAM_BOT_TOKEN=' .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"'"'"' \r')
  chat=$(grep -E '^TELEGRAM_CHAT_ID=' .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"'"'"' \r')
  if [[ -n "$token" && -n "$chat" ]]; then
    if curl -sS -m 15 -o /dev/null -w '%{http_code}' \
         "https://api.telegram.org/bot${token}/sendMessage" \
         --data-urlencode "chat_id=${chat}" \
         --data-urlencode "text=${msg}" 2>/dev/null | grep -q '^200$'; then
      sent=1
    fi
  fi
  if [[ $sent -eq 0 ]]; then
    # 送不出去本身就是情报 —— 屏幕上留痕, 别静默
    run_to 10 osascript -e "display notification \"${msg//\"/}\" with title \"CAT host-watchdog\" sound name \"Basso\"" \
      >/dev/null 2>&1 || true
    log "NOTIFY-FALLBACK (telegram unreachable): $msg"
  else
    log "NOTIFY-SENT: $msg"
  fi
}

# --- 只读查询: 直接打 PG, 绕开 backend (backend 卡死时仍要能查) -------------
sql() {
  run_to 20 $DC exec -T postgres sh -c \
    'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -X -A -t -F "|" -v ON_ERROR_STOP=1' \
    <<< "$1" 2>/dev/null
}

# --- signal_cycle 是否错过「最近一个已到期」的排程 -------------------------
# 镜像 backend/app/modules/system/watchdog.py (review #28): 从今天 (ET) 往回找最近一个
# XNYS 交易日, 其 21:30 UTC + 2h 宽限已过 → 心跳早于该日 21:30 UTC 才算错过。
# 固定 26h 会在每个周末/假日误报。用宿主 research venv, 不依赖可能已死的 backend 容器。
# 输出 stale / ok; 其他 (venv 坏、超时) 由调用方退回 26h 粗判。
signal_cycle_verdict() {
  run_to 20 quant/.venv/bin/python - "$1" "$2" <<'PY'
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from quant.data import calendar as qcal

beat, now = float(sys.argv[1]), float(sys.argv[2])
probe = datetime.fromtimestamp(now, ZoneInfo("America/New_York")).date()
verdict = "ok"
for _ in range(10):
    if qcal.is_trading_day(probe):
        run_at = datetime(probe.year, probe.month, probe.day, 21, 30, tzinfo=timezone.utc).timestamp()
        if run_at + 2 * 3600 <= now:
            verdict = "stale" if beat < run_at else "ok"
            break
    probe -= timedelta(days=1)
print(verdict)
PY
}

issues=()
add() { issues+=("$1"); }

# 1) Docker daemon 本身 (2026-09-11 是 VM 整个崩掉; 2026-10-01 是 VM 卡死、调用不返回)
docker_ok=0
run_to 20 docker info >/dev/null 2>&1
case $? in
  0)   docker_ok=1 ;;
  124) add "Docker daemon 无响应（超时 20s，VM 可能卡死）— 整个 CAT 停摆中" ;;
  *)   add "Docker daemon 不可达 — 整个 CAT 停摆中" ;;
esac

# daemon 都没了, 后面的检查全无意义 —— 但仍要走下面的判定 + 冷却, 否则每 5 分钟发一次
if (( docker_ok )); then
  # 2) 六个容器
  for c in $CONTAINERS; do
    st=$(run_to 10 docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null)
    case $? in
      0)   ;;
      124) st="查询超时 10s" ;;
      *)   st="missing" ;;
    esac
    [[ "$st" == "running" ]] || add "容器 $c = $st"
  done

  # 3) PG 可连
  run_to 20 $DC exec -T postgres pg_isready -q >/dev/null 2>&1
  case $? in
    0)   ;;
    124) add "PostgreSQL 探测超时 (20s)" ;;
    *)   add "PostgreSQL 不接受连线" ;;
  esac

  # 4) backend HTTP 健康 (宿主 8000 的 IPv4 被别的 php 项目占着, 一律容器内探)
  run_to 20 $DC exec -T backend python -c \
    "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health', timeout=5)" \
    >/dev/null 2>&1
  case $? in
    0)   ;;
    124) add "backend /api/health 探测超时 (20s)" ;;
    *)   add "backend /api/health 不通 (可能卡在 startup — 2026-09-11 那一类)" ;;
  esac

  # 5) 心跳新鲜度 — 直接读 PG, 不经 backend
  hb=$(sql "select name, round(extract(epoch from (now() - last_beat_at))/60) from heartbeats where name in ('worker','market_stream');")
  rc=$?
  if (( rc == 124 )); then
    add "读 heartbeats 超时 (20s) — PG 可能卡住"
  elif [[ -z "$hb" ]]; then
    add "读不到 heartbeats 表 (PG 或 schema 有问题)"
  else
    while IFS='|' read -r name age_min; do
      [[ -z "$name" ]] && continue
      case "$name" in
        worker)        (( ${age_min%.*} > 10 )) && add "worker 心跳停了 ${age_min%.*} 分钟" ;;
        market_stream) (( ${age_min%.*} > 30 )) && add "market_stream 心跳停了 ${age_min%.*} 分钟" ;;
      esac
    done <<< "$hb"
  fi

  # 6) signal_cycle: 错过最近一次排程, 或跑了但 fail-closed (0 推荐)
  sc=$(sql "select extract(epoch from last_beat_at)::bigint, coalesce(meta->>'fail_closed',''), coalesce(meta->>'recs','?') from heartbeats where name='signal_cycle';")
  rc=$?
  if (( rc == 124 )); then
    add "读 signal_cycle 心跳超时 (20s)"
  elif [[ -n "$sc" ]]; then
    IFS='|' read -r sc_beat sc_fc sc_recs <<< "$sc"
    sc_now=$(date +%s)
    sc_age=$(( (sc_now - sc_beat + 1800) / 3600 ))
    case "$(signal_cycle_verdict "$sc_beat" "$sc_now" 2>/dev/null)" in
      stale) add "signal_cycle 已 ${sc_age} 小时没跑成功" ;;
      ok)    ;;
      *)     (( sc_age > 26 )) && add "signal_cycle 已 ${sc_age} 小时没跑成功（日历不可用，按 26h 粗判）" ;;
    esac
    [[ -n "$sc_fc" ]] && add "上一次 signal_cycle fail-closed: ${sc_fc} (recs=${sc_recs})"
  fi
fi

# --- 判定 + 冷却 -----------------------------------------------------------
if [[ "${1:-}" == "--test" ]]; then
  notify "✅ CAT host-watchdog 测试告警 — 送达路径正常 ($(date '+%F %H:%M'))"
  exit 0
fi

prev_sig=$(cut -d' ' -f2- "$STATE_FILE" 2>/dev/null || echo "")
prev_ts=$(cut -d' ' -f1 "$STATE_FILE" 2>/dev/null || echo 0)
now_ts=$(date +%s)

if (( ${#issues[@]} == 0 )); then
  # --dry 不碰 state / 不发通知, 否则会抢走 launchd 那一轮该发的「恢复」
  if [[ "${1:-}" == "--dry" ]]; then
    echo "OK — 无问题"
    [[ -n "$prev_sig" ]] && echo "(上一轮问题待恢复通知: $prev_sig)"
    exit 0
  fi
  [[ -n "$prev_sig" ]] && { notify "✅ CAT 恢复正常 — 上一轮的问题已消失。"; log "RECOVERED"; }
  : > "$STATE_FILE"
  exit 0
fi

sig=$(printf '%s;' "${issues[@]}")
body=$(printf '• %s\n' "${issues[@]}")
msg="🚨 CAT host-watchdog ($(date '+%F %H:%M'))
${body}"

if [[ "${1:-}" == "--dry" ]]; then
  echo "$msg"
  exit 1
fi

if [[ "$sig" == "$prev_sig" ]] && (( now_ts - prev_ts < COOLDOWN_SEC )); then
  log "SUPPRESSED (cooldown): $sig"
else
  notify "$msg"
  echo "$now_ts $sig" > "$STATE_FILE"
fi
exit 1
