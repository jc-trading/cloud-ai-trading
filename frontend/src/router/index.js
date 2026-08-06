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
        name: 'Recommendations',
        component: () => import('@/views/Recommendations.vue'),
      },
      {
        // Legacy link support
        path: 'recommendations',
        redirect: '/',
      },
      {
        path: 'sim',
        name: 'SimAccount',
        component: () => import('@/views/SimAccount.vue'),
      },
      {
        path: 'night-watch',
        name: 'NightWatch',
        component: () => import('@/views/NightWatch.vue'),
      },
      {
        path: 'llm-log',
        name: 'LlmLog',
        component: () => import('@/views/LlmLog.vue'),
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
        path: 'settings',
        name: 'Settings',
        component: () => import('@/views/Settings.vue'),
      },
      {
        path: 'settings/exchange',
        name: 'ExchangeSettings',
        component: () => import('@/views/ExchangeSettings.vue'),
      },
      {
        // Catch-all for deleted/unknown routes (/portfolio, /trading, ...):
        // lowest match score, so it only fires when nothing above matched.
        path: ':pathMatch(.*)*',
        redirect: '/',
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
    return next({ name: 'Recommendations' })
  }

  next()
})

export default router
