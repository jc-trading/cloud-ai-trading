/**
 * Night-watch (深夜股票检测) API — the G2 observation run's per-night ledger:
 * cycle heartbeats + protections "now" panel, and one row per US session
 * (signal recs/shortlist, system-account orders, LLM day cost, EOD equity).
 * Read-only aggregation over existing tables. Shared axios client.
 */
import client from './client'

export const getNightWatchLog = (days = 30) =>
  client.get('/nightwatch/log', { params: { days } })
