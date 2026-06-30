import client from './client'

export const marketApi = {
  getTickers: (symbols = null) =>
    client.get('/market/tickers', symbols ? { params: { symbols: symbols.join(',') } } : {}),
  getStockTickers: (symbols = null) =>
    client.get('/market/tickers/stocks', symbols ? { params: { symbols: symbols.join(',') } } : {}),
  getSymbol: (symbol) => client.get(`/market/${encodeURIComponent(symbol)}`),
  getCandles: (symbol, params) => client.get(`/market/${encodeURIComponent(symbol)}/candles`, { params }),
  searchSymbols: (q) => client.get('/market/search', { params: { q } }),
  searchStocks: (q) => client.get('/market/search/stocks', { params: { q } }),
  searchCrypto: (q) => client.get('/market/search/crypto', { params: { q } }),
}

export const watchlistApi = {
  // Default watchlist (auto-created, most common)
  getDefault: () => client.get('/watchlists/default'),
  getDefaultWithPrices: () => client.get('/watchlists/default/prices'),
  addToDefault: (data) => client.post('/watchlists/default/items', data),
  removeFromDefault: (itemId) => client.delete(`/watchlists/default/items/${itemId}`),

  // Named watchlists (multi-list feature)
  list: () => client.get('/watchlists'),
  create: (data) => client.post('/watchlists', data),
  remove: (id) => client.delete(`/watchlists/${id}`),
  getItems: (id) => client.get(`/watchlists/${id}/items`),
  addItem: (id, data) => client.post(`/watchlists/${id}/items`, data),
  removeItem: (id, itemId) => client.delete(`/watchlists/${id}/items/${itemId}`),
}

export const analysisApi = {
  list: (params) => client.get('/analysis', { params }),
  getBySymbol: (symbol) => client.get(`/analysis/${symbol}`),
  trigger: (symbol) => client.post('/analysis/trigger', { symbol }),
  getStats: () => client.get('/analysis/stats'),
}

export const tradingApi = {
  placeOrder: (data) => client.post('/trading/order', data),
  getOrders: (params) => client.get('/trading/orders', { params }),
  getPositions: () => client.get('/trading/positions'),
  getHistory: (params) => client.get('/trading/history', { params }),
}

export const simulateApi = {
  getPortfolio: () => client.get('/simulate/portfolio'),
  getStats: () => client.get('/simulate/stats'),
  getHistory: (params) => client.get('/simulate/history', { params }),
  reset: () => client.post('/simulate/reset'),
}

export const strategyApi = {
  list: () => client.get('/strategies'),
  create: (data) => client.post('/strategies', data),
  update: (id, data) => client.put(`/strategies/${id}`, data),
  remove: (id) => client.delete(`/strategies/${id}`),
  backtest: (id) => client.post(`/strategies/${id}/backtest`),
  activate: (id) => client.post(`/strategies/${id}/activate`),
  deactivate: (id) => client.post(`/strategies/${id}/deactivate`),
}
