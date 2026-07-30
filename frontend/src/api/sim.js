/**
 * Sim-ledger API (Direction v3) — recommendation feed, practice/system
 * accounts, manual practice trades. Shared axios client (JWT + auto-refresh).
 */
import client from './client'

// Latest published feed (optionally a specific trade_date, YYYY-MM-DD).
export const getRecommendations = (forDate = null) => {
  const params = {}
  if (forDate) params.for_date = forDate
  return client.get('/sim/recommendations', { params })
}

// Caller's practice account (auto-created) + read-only 对照账户 view.
export const getSimAccount = () => client.get('/sim/account')

// Manual practice trade. Buys REQUIRE a stop below the current price.
export const placeSimTrade = ({ symbol, side, qty, stop = null }) =>
  client.post('/sim/trade', { symbol, side, qty, stop })
