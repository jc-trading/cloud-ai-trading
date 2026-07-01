/**
 * Decision feed API — the unified go/no-go/watch verdict per tracked symbol
 * (crypto + equity). Uses the shared axios `client` (JWT + auto-refresh).
 */
import client from './client'

// Latest verdict per tracked symbol. Optional assetClass ('crypto' | 'equity')
// narrows the feed server-side.
export const getDecisions = (assetClass = null) => {
  const params = {}
  if (assetClass) params.asset_class = assetClass
  return client.get('/decisions', { params })
}

// Full verdict history for one symbol, newest-first.
export const getDecisionHistory = (symbol, limit = 100) =>
  client.get(`/decisions/${encodeURIComponent(symbol)}`, { params: { limit } })
