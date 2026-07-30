import client from './client'

export const marketApi = {
  getStockTickers: (symbols = null) =>
    client.get('/market/tickers/stocks', symbols ? { params: { symbols: symbols.join(',') } } : {}),
  getSymbol: (symbol) => client.get(`/market/${encodeURIComponent(symbol)}`),
  getCandles: (symbol, params) => client.get(`/market/${encodeURIComponent(symbol)}/candles`, { params }),
  searchStocks: (q) => client.get('/market/search/stocks', { params: { q } }),
}

export const watchlistApi = {
  // Default watchlist (auto-created)
  getDefault: () => client.get('/watchlists/default'),
  getDefaultWithPrices: () => client.get('/watchlists/default/prices'),
  addToDefault: (data) => client.post('/watchlists/default/items', data),
  removeFromDefault: (itemId) => client.delete(`/watchlists/default/items/${itemId}`),
}
