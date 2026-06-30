import client from './client'

export const authApi = {
  register: (data) => client.post('/auth/register', data),
  login: (data) => client.post('/auth/login', data),
  refresh: (refreshToken) => client.post('/auth/refresh', { refresh_token: refreshToken }),
  getMe: () => client.get('/auth/me'),
  updateMe: (data) => client.put('/auth/me', data),

  // Admin
  listUsers: (params) => client.get('/auth/users', { params }),
  updateUserRole: (userId, role) => client.put(`/auth/users/${userId}/role`, { role }),
}
