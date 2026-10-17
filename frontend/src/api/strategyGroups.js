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
