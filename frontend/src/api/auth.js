import client from './client'

// skipAuthRefresh: a 401 from these endpoints (bad password, expired refresh)
// must surface to the caller instead of entering the token-refresh flow —
// see the response interceptor in client.js.
export const authApi = {
  register: (data) => client.post('/auth/register', data, { skipAuthRefresh: true }),
  login: (data) => client.post('/auth/login', data, { skipAuthRefresh: true }),
  refresh: (refreshToken) =>
    client.post('/auth/refresh', { refresh_token: refreshToken }, { skipAuthRefresh: true }),
  getMe: () => client.get('/auth/me'),
  updateMe: (data) => client.put('/auth/me', data),

  // Role management is deliberately API/psql-only (no UI) — backend endpoints retained.
}
