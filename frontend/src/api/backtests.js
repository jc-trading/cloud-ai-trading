import client from './client'
export const createBacktest = (body) => client.post('/backtests', body)
export const listBacktests = ({ groupId = null, status = null, limit = 50 } = {}) => {
  const params = { limit }
  if (groupId) params.group_id = groupId
  if (status) params.status = status
  return client.get('/backtests', { params })
}
export const getBacktest = (id) => client.get(`/backtests/${id}`)
export const getBacktestEquity = (id) => client.get(`/backtests/${id}/equity`)
export const cancelBacktest = (id) => client.post(`/backtests/${id}/cancel`)
export const compareBacktests = ({ groupIds = [], runIds = [], start = null } = {}) =>
  client.get('/backtests/compare', { params: {
    group_ids: groupIds.length ? groupIds.join(',') : undefined,
    run_ids: runIds.length ? runIds.join(',') : undefined,
    start: start || undefined } })
