/**
 * Strategy-group API — the groups every per-strategy view is scoped to.
 * There is no default group: callers pick one and pass its id.
 * Shared axios client (JWT + auto-refresh).
 */
import client from './client'

export const listStrategyGroups = (status = null) => {
  const params = {}
  if (status) params.status = status
  return client.get('/strategy-groups', { params })
}

export const getStrategyGroup = (id) => client.get(`/strategy-groups/${id}`)
export const getRegistry = () => client.get('/strategy-groups/registry')
export const createGroup = (body) => client.post('/strategy-groups', body)
export const cloneGroup = (id, body) => client.post(`/strategy-groups/${id}/clone`, body)
export const setGroupStatus = (id, status) => client.patch(`/strategy-groups/${id}/status`, { status })
export const getGroupRuns = (id, { days = 30, cycle = null } = {}) => {
  const params = { days }
  if (cycle) params.cycle = cycle
  return client.get(`/strategy-groups/${id}/runs`, { params })
}
// ~570 MB per request server-side and no concurrency cap: call only from an explicit click.
export const getGroupScreen = (id, date = null) =>
  client.get(`/strategy-groups/${id}/screen`, { params: date ? { date } : {}, timeout: 120000 })
