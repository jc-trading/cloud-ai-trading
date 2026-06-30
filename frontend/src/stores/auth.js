import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { authApi } from '../api/auth'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('access_token') || '')
  const refreshToken = ref(localStorage.getItem('refresh_token') || '')
  const user = ref(null)

  const isAuthenticated = computed(() => !!token.value)
  const accessToken = computed(() => token.value)
  const isAdmin = computed(() => {
    const role = user.value?.role
    return role === 'admin' || role === 'super_admin'
  })

  const setSession = (data) => {
    token.value = data.access_token
    refreshToken.value = data.refresh_token
    user.value = data.user
    localStorage.setItem('access_token', data.access_token)
    localStorage.setItem('refresh_token', data.refresh_token)
  }

  const logout = () => {
    token.value = ''
    refreshToken.value = ''
    user.value = null
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
  }

  const login = async (email, password) => {
    const response = await authApi.login({ email, password })
    setSession(response.data)
    return response
  }

  const register = async (data) => {
    const response = await authApi.register(data)
    setSession(response.data)
    return response
  }

  const fetchUser = async () => {
    if (!token.value) return null
    const response = await authApi.getMe()
    user.value = response.data
    return user.value
  }

  return {
    token,
    refreshToken,
    accessToken,
    user,
    isAuthenticated,
    isAdmin,
    setSession,
    logout,
    login,
    register,
    fetchUser,
  }
})
