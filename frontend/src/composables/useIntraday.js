export const TIER_COLORS = { held_1m: 'orange', shortlist_1m: 'cyan', held_1d: 'yellow', configured: 'blue', watchlist: 'gray' }

export const tierColor = (t) => TIER_COLORS[t] || 'gray'

// 45 = the engine's STREAM_HEALTHY_AGE_S
export const streamAgeColor = (ageS) => {
  if (ageS == null) return 'gray'
  if (ageS < 45) return 'green'
  if (ageS < 300) return 'yellow'
  return 'red'
}

const LOCKED_1M = ['holding_style', 'session']

export const blankTrade1m = (keys1m) =>
  Object.fromEntries(Object.keys(keys1m).filter((k) => !LOCKED_1M.includes(k)).map((k) => [k, '']))

export const toTradeSettingsJson1m = (m) => {
  const out = {}
  for (const [k, v] of Object.entries(m)) {
    if (LOCKED_1M.includes(k) || v === '' || v == null) continue
    out[k] = v
  }
  return out
}

export const watchBudgetUsed = (details) => details
  .filter((g) => g.status === 'active' && g.timeframe === '1m')
  .reduce((sum, g) => sum + Number(g.params_json?.rank?.watch_max ?? 0), 0)

export const backtestBody = (form, timeframe, groupId) => {
  const body = { group_id: groupId, kind: form.kind, start: form.start, end: form.end }
  if (form.kind !== 'walk_forward') return body
  if (timeframe === '1m') {
    if (form.is_sessions !== '') body.is_sessions = form.is_sessions
    if (form.oos_sessions !== '') body.oos_sessions = form.oos_sessions
    return body
  }
  Object.assign(body, { is_years: form.is_years, oos_years: form.oos_years })
  return body
}

export const backtestFieldOf = (piece) => {
  if (piece.startsWith('1m runs: start')) return 'start'
  return piece.match(/^(kind|start|end|is_years|oos_years|is_sessions|oos_sessions|grid)/)?.[1] || null
}
