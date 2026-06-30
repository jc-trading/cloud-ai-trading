import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const routes = [
  // Auth routes (no layout)
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/Login.vue'),
    meta: { guest: true },
  },
  {
    path: '/register',
    name: 'Register',
    component: () => import('@/views/Register.vue'),
    meta: { guest: true },
  },

  // App routes (with layout)
  {
    path: '/',
    component: () => import('@/components/layout/AppLayout.vue'),
    meta: { requiresAuth: true },
    children: [
      {
        path: '',
        name: 'Dashboard',
        component: () => import('@/views/Dashboard.vue'),
      },
      {
        path: 'market',
        name: 'Market',
        component: () => import('@/views/Market.vue'),
      },
      {
        path: 'market/:symbol',
        name: 'SymbolDetail',
        component: () => import('@/views/SymbolDetail.vue'),
        props: true,
      },
      {
        path: 'watchlist',
        name: 'Watchlist',
        component: () => import('@/views/Watchlist.vue'),
      },
      {
        path: 'analysis',
        name: 'Analysis',
        component: () => import('@/views/Analysis.vue'),
      },
      {
        path: 'strategies',
        name: 'Strategies',
        component: () => import('@/views/StrategyBuilder.vue'),
      },
      {
        path: 'trading',
        name: 'Trading',
        component: () => import('@/views/Trading.vue'),
      },
      {
        path: 'signals',
        name: 'Signals',
        component: () => import('@/views/Signals.vue'),
      },
      {
        path: 'portfolio',
        name: 'Portfolio',
        component: () => import('@/views/Portfolio.vue'),
      },
      {
        path: 'simulate',
        name: 'Simulate',
        component: () => import('@/views/Simulate.vue'),
      },
      {
        path: 'settings',
        name: 'Settings',
        component: () => import('@/views/Settings.vue'),
      },
      {
        path: 'settings/exchange',
        name: 'ExchangeSettings',
        component: () => import('@/views/ExchangeSettings.vue'),
      },

      // Admin routes
      {
        path: 'admin/users',
        name: 'AdminUsers',
        component: () => import('@/views/admin/Users.vue'),
        meta: { requiresAdmin: true },
      },
      {
        path: 'admin/system',
        name: 'AdminSystem',
        component: () => import('@/views/admin/System.vue'),
        meta: { requiresAdmin: true },
      },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// Navigation guard
router.beforeEach(async (to, from, next) => {
  const auth = useAuthStore()

  // Fetch user if token exists but user not loaded
  if (auth.accessToken && !auth.user) {
    await auth.fetchUser()
  }

  if (to.meta.requiresAuth && !auth.isAuthenticated) {
    return next({ name: 'Login', query: { redirect: to.fullPath } })
  }

  if (to.meta.guest && auth.isAuthenticated) {
    return next({ name: 'Dashboard' })
  }

  if (to.meta.requiresAdmin && !auth.isAdmin) {
    return next({ name: 'Dashboard' })
  }

  next()
})

export default router
