/**
 * P5 intraday reads; watch is admin-only.
 */
import client from './client'

export const getWatch = (session = null) => client.get('/intraday/watch', { params: session ? { session } : {} })
