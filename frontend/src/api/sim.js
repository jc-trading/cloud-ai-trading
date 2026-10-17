/**
 * Sim-ledger API (Direction v3) — per-group recommendation feed and account,
 * the caller's practice account, manual practice trades. Shared axios client
 * (JWT + auto-refresh).
 */
import client from './client'

// A group's latest published feed (optionally a specific trade_date, YYYY-MM-DD).
export const getRecommendations = (groupId, forDate = null) => {
  const params = { group_id: groupId }
  if (forDate) params.for_date = forDate
  return client.get('/sim/recommendations', { params })
}

// Caller's practice account (auto-created).
export const getSimAccount = () => client.get('/sim/account')

// A strategy group's account (read-only view).
export const getGroupAccount = (groupId) =>
  client.get('/sim/group-account', { params: { group_id: groupId } })

// Manual practice trade. Buys REQUIRE a stop below the current price.
export const placeSimTrade = ({ symbol, side, qty, stop = null }) =>
  client.post('/sim/trade', { symbol, side, qty, stop })
