/**
 * LLM usage-log API (Direction v3) — every LLM call the system makes, with
 * platform/model/tokens/snapshotted unit prices/USD cost. Shared axios client.
 */
import client from './client'

// Summary tiles (all-time + today + per-model + per-day) computed over ALL
// rows server-side, plus the most-recent `limit` call rows for the table.
export const getLlmLog = (limit = 500) =>
  client.get('/llm/log', { params: { limit } })
