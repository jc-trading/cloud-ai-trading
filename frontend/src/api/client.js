/**
 * Axios HTTP client with JWT auth interceptors.
 */
import axios from 'axios'

const API_BASE = '/api/v1'

const client = axios.create({
  baseURL: API_BASE,
  timeout: 60000,  // 60s — market data batch fetch can be slow on first call
  headers: {
    'Content-Type': 'application/json',
  },
})

// Request interceptor: attach JWT token
client.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// Response interceptor: handle 401 → auto refresh token
client.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config

    // A 401 from the auth endpoints themselves (bad password, expired refresh)
    // must surface to the caller — routing it into the refresh flow ends in
    // window.location redirect that reloads the page and eats the error
    // message (QA finding #2).
    const authPath = /\/auth\/(login|register|refresh)/.test(originalRequest?.url || '')

    if (error.response?.status === 401 && !originalRequest._retry && !authPath) {
      originalRequest._retry = true

      try {
        const refreshToken = localStorage.getItem('refresh_token')
        if (!refreshToken) throw new Error('No refresh token')

        const { data } = await axios.post(`${API_BASE}/auth/refresh`, {
          refresh_token: refreshToken,
        })

        localStorage.setItem('access_token', data.access_token)
        localStorage.setItem('refresh_token', data.refresh_token)

        originalRequest.headers.Authorization = `Bearer ${data.access_token}`
        return client(originalRequest)
      } catch (refreshError) {
        // Refresh failed → logout
        localStorage.removeItem('access_token')
        localStorage.removeItem('refresh_token')
        window.location.href = '/login'
        return Promise.reject(refreshError)
      }
    }

    return Promise.reject(error)
  }
)

export default client
