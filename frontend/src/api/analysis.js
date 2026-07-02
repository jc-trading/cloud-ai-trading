/**
 * AI Analysis API. Manual on-demand analysis of a symbol.
 * Uses the shared axios `client` (JWT + auto-refresh).
 */
import client from './client'

// Run an analysis. payload: { symbol, exchange_type='binance', strategy_id? }
export const runAnalysis = (payload) => client.post('/analysis', payload)

// List recent analyses (newest-first), optional limit.
export const listAnalyses = (limit = 20) => client.get('/analysis', { params: { limit } })

// Aggregate summary (counts, avg confidence, cost, …).
export const getAnalysisSummary = () => client.get('/analysis/summary')

// Latest analysis for one symbol.
export const getLatestAnalysis = (symbol) =>
  client.get(`/analysis/latest/${encodeURIComponent(symbol)}`)

export const getAnalysis = (id) => client.get(`/analysis/${id}`)
