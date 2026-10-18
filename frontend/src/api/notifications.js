/**
 * Notification settings API (admin only) — channels, routing rules and the
 * delivery log. Secrets never pass through here: a channel names the env key
 * that holds its secret and the API only reports whether it is set.
 */
import client from './client'

export const getCatalog = () => client.get('/notifications/catalog')

export const listChannels = () => client.get('/notifications/channels')
export const createChannel = (body) => client.post('/notifications/channels', body)
export const updateChannel = (id, body) => client.patch(`/notifications/channels/${id}`, body)
export const deleteChannel = (id) => client.delete(`/notifications/channels/${id}`)
// A failing SMTP test retries for up to ~76s — longer than the client default.
export const testChannel = (id) =>
  client.post(`/notifications/channels/${id}/test`, null, { timeout: 120000 })

export const listRules = () => client.get('/notifications/rules')
export const createRule = (body) => client.post('/notifications/rules', body)
export const updateRule = (id, body) => client.patch(`/notifications/rules/${id}`, body)
export const deleteRule = (id) => client.delete(`/notifications/rules/${id}`)

export const getNotificationLog = (limit = 50) =>
  client.get('/notifications/log', { params: { limit } })
