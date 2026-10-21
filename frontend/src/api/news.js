import client from './client'
export const getNewsItems = ({ status = null, symbol = null, action = null, origin = null, cursor = null, limit = 50 } = {}) => {
  const params = { limit }
  if (status) params.status = status
  if (symbol) params.symbol = symbol
  if (action) params.action = action
  if (origin) params.origin = origin
  if (cursor) params.cursor = cursor
  return client.get('/news/items', { params })
}
export const getNewsItem = (id) => client.get(`/news/items/${id}`)
export const getReviewQueue = (includeUpcoming = false) =>
  client.get('/news/review-queue', { params: includeUpcoming ? { include_upcoming: true } : {} })
export const reviewSymbol = (id, body) => client.patch(`/news/symbols/${id}/review`, body)
export const getNewsSources = (kind = null) => {
  const params = {}
  if (kind) params.kind = kind
  return client.get('/news/sources', { params })
}
export const setNewsSource = (id, body) => client.patch(`/news/sources/${id}`, body)
