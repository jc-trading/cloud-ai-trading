/**
 * System monitoring API. Uses the shared axios `client` (JWT + auto-refresh).
 */
import client from './client'

export const getMetrics = () => client.get('/system/metrics')

export const getLogs = (category = null, level = null, limit = 50) => {
  const params = new URLSearchParams()
  if (category) params.append('category', category)
  if (level) params.append('level', level)
  params.append('limit', limit)
  return client.get(`/system/logs?${params.toString()}`)
}

export const getTaskStatus = () => client.get('/system/tasks')
export const getHealth = () => client.get('/system/health')
export const syncTaskStatus = () => client.post('/system/tasks/sync')
export const cleanupLogs = (days = 30) => client.post('/system/logs/cleanup', null, { params: { days } })
export const cleanupMetrics = (days = 30) => client.post('/system/metrics/cleanup', null, { params: { days } })
