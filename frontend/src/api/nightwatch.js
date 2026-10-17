/**
 * Night-watch (深夜股票检测) API — the G2 observation run's per-night ledger:
 * cycle heartbeats + protections "now" panel, and one row per US session
 * (signal recs/shortlist, the group account's orders, LLM day cost, EOD
 * equity), scoped to one strategy group.
 * Read-only aggregation over existing tables. Shared axios client.
 */
import client from './client'

export const getNightWatchLog = (groupId, days = 30) =>
  client.get('/nightwatch/log', { params: { group_id: groupId, days } })
