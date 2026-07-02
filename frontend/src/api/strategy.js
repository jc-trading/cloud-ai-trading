/**
 * Strategy API — CRUD for quant strategies. Uses the shared axios `client`.
 * NOTE: backtesting is NOT supported server-side (the backtest router is not
 * mounted), so there is no runBacktest() here by design.
 */
import client from './client'

export const listStrategies = () => client.get('/strategies')
export const createStrategy = (payload) => client.post('/strategies', payload)
export const getStrategy = (id) => client.get(`/strategies/${id}`)
export const updateStrategy = (id, payload) => client.put(`/strategies/${id}`, payload)
export const deleteStrategy = (id) => client.delete(`/strategies/${id}`)
export const toggleStrategy = (id) => client.post(`/strategies/${id}/toggle`)
