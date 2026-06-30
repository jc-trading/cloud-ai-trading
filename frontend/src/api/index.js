import axios from 'axios'
import { useAuthStore } from '../stores/auth'

const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api/v1'

const api = axios.create({
  baseURL: API_BASE,
  headers: {
    'Content-Type': 'application/json'
  }
})

// 请求拦截器：添加 JWT token
api.interceptors.request.use((config) => {
  const authStore = useAuthStore()
  if (authStore.token) {
    config.headers.Authorization = `Bearer ${authStore.token}`
  }
  return config
})

// 响应拦截器
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      const authStore = useAuthStore()
      authStore.logout()
      window.location.href = '/login'
    }
    return Promise.reject(error)
  }
)

// 认证 API
export const login = (email, password) => 
  api.post('/auth/login', { email, password })

export const register = (name, email, password) =>
  api.post('/auth/register', { name, email, password })

// 系统监控 API
export const getMetrics = () =>
  api.get('/system/metrics')

export const getLogs = (category = null, level = null, limit = 50) => {
  const params = new URLSearchParams()
  if (category) params.append('category', category)
  if (level) params.append('level', level)
  params.append('limit', limit)
  return api.get(`/system/logs?${params.toString()}`)
}

export const getTaskStatus = () =>
  api.get('/system/tasks')

export const getHealth = () =>
  api.get('/system/health')

export const syncTaskStatus = () =>
  api.post('/system/tasks/sync')

export const cleanupLogs = (days = 30) =>
  api.post('/system/logs/cleanup', null, { params: { days } })

export const cleanupMetrics = (days = 30) =>
  api.post('/system/metrics/cleanup', null, { params: { days } })

export default api
