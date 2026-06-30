import api from './index'

/**
 * Trading API Client
 * Handles all trading-related API calls (signals, portfolio, trades)
 */

// ────────────────────────────────────────────────────────────
// Trading Signals API
// ────────────────────────────────────────────────────────────

/**
 * Get recent trading signals
 * @param {number} limit - Maximum number of signals to fetch (default: 50)
 * @returns {Promise} Array of trading signals
 */
export const getSignals = (limit = 50) =>
  api.get('/trading/signals', { params: { limit } })

/**
 * Get signals for a specific symbol
 * @param {string} symbol - Trading pair symbol (e.g., 'BTCUSDT')
 * @param {number} limit - Maximum number of signals to fetch (default: 20)
 * @returns {Promise} Array of signals for the symbol
 */
export const getSignalsBySymbol = (symbol, limit = 20) =>
  api.get(`/trading/signals/${symbol}`, { params: { limit } })

// ────────────────────────────────────────────────────────────
// Portfolio API (Simulated Trading)
// ────────────────────────────────────────────────────────────

/**
 * Get simulated trading portfolio
 * @returns {Promise} Portfolio data with stats
 */
export const getPortfolio = () =>
  api.get('/trading/portfolio/simulate')

/**
 * Reset simulated portfolio to initial balance
 * @returns {Promise} Reset portfolio data
 */
export const resetPortfolio = () =>
  api.post('/trading/portfolio/simulate/reset')

/**
 * Get portfolio statistics
 * @returns {Promise} Portfolio stats including P&L, win rate, etc.
 */
export const getPortfolioStats = () =>
  api.get('/trading/portfolio/stats')

// ────────────────────────────────────────────────────────────
// Trades API
// ────────────────────────────────────────────────────────────

/**
 * Get list of trades with filters
 * @param {Object} filters - Filter options
 * @param {string} filters.symbol - Filter by symbol
 * @param {string} filters.side - Filter by side (buy/sell)
 * @param {string} filters.status - Filter by status
 * @param {string} filters.tradingMode - Filter by mode (live/simulate)
 * @param {number} filters.limit - Max results (default: 50)
 * @param {number} filters.offset - Pagination offset (default: 0)
 * @returns {Promise} Array of trades
 */
export const getTrades = (filters = {}) => {
  const params = new URLSearchParams()
  if (filters.symbol) params.append('symbol', filters.symbol)
  if (filters.side) params.append('side', filters.side)
  if (filters.status) params.append('status', filters.status)
  if (filters.tradingMode) params.append('trading_mode', filters.tradingMode)
  params.append('limit', filters.limit || 50)
  params.append('offset', filters.offset || 0)
  return api.get(`/trading/trades?${params.toString()}`)
}

/**
 * Get a specific trade by ID
 * @param {string} tradeId - Trade ID
 * @returns {Promise} Trade data
 */
export const getTrade = (tradeId) =>
  api.get(`/trading/trades/${tradeId}`)

/**
 * Get trade summary statistics
 * @param {string} tradingMode - Mode to filter (live/simulate/all)
 * @returns {Promise} Trade summary data
 */
export const getTradeSummary = (tradingMode = null) => {
  const params = tradingMode ? { trading_mode: tradingMode } : {}
  return api.get('/trading/trades/summary', { params })
}

/**
 * Place a new trade
 * @param {Object} tradeData - Trade data
 * @param {string} tradeData.symbol - Trading pair
 * @param {string} tradeData.side - buy or sell
 * @param {number} tradeData.price - Entry price
 * @param {number} tradeData.quantity - Order quantity
 * @param {string} tradeData.tradingMode - live or simulate
 * @returns {Promise} New trade data
 */
export const placeTrade = (tradeData) =>
  api.post('/trading/trades', {
    ...tradeData,
    trading_mode: tradeData.trading_mode || tradeData.tradingMode || 'simulate',
  })

/**
 * Close a trade (simulated trading only)
 * @param {string} tradeId - Trade ID to close
 * @returns {Promise} Updated trade data
 */
export const closeTrade = (tradeId) =>
  api.post(`/trading/trades/${tradeId}/close`)

// ────────────────────────────────────────────────────────────
// Real-time Data Stream (WebSocket Ready)
// ────────────────────────────────────────────────────────────

/**
 * Subscribe to real-time signals updates
 * This would be used with WebSocket integration
 * @returns {string} Subscription endpoint
 */
export const getSignalsStreamEndpoint = () =>
  null

/**
 * Subscribe to portfolio updates
 * @returns {string} Subscription endpoint
 */
export const getPortfolioStreamEndpoint = () =>
  null

export default {
  // Signals
  getSignals,
  getSignalsBySymbol,

  // Portfolio
  getPortfolio,
  resetPortfolio,
  getPortfolioStats,

  // Trades
  getTrades,
  getTrade,
  getTradeSummary,
  placeTrade,
  closeTrade,

  // Streaming
  getSignalsStreamEndpoint,
  getPortfolioStreamEndpoint,
}
