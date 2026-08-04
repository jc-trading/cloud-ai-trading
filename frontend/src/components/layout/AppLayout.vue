<template>
  <div class="jd-layout">

    <!-- ── Sidebar ─────────────────────────────────────────── -->
    <aside class="jd-sidebar">

      <!-- Logo -->
      <router-link to="/" class="jd-sidebar-logo">
        <div class="jd-logo-icon">
          <i class="pi pi-chart-line"></i>
        </div>
        <div class="jd-logo-text">
          <div class="jd-logo-name">CloudAI</div>
          <div class="jd-logo-sub">Trading Portal</div>
        </div>
      </router-link>

      <!-- Nav -->
      <nav class="jd-sidebar-nav">

        <div class="jd-nav-section">
          <div class="jd-nav-label">Overview</div>
          <SidebarItem to="/" icon="pi-compass" label="Recommendations" :isActive="route.path === '/'" />
          <SidebarItem to="/sim" icon="pi-wallet" label="Sim Accounts" :isActive="route.path === '/sim'" />
          <SidebarItem to="/llm-log" icon="pi-bolt" label="LLM Log" :isActive="route.path === '/llm-log'" />
        </div>

        <div class="jd-nav-section">
          <div class="jd-nav-label">Market Data</div>
          <SidebarItem to="/market" icon="pi-globe" label="Market Overview" :isActive="route.path.startsWith('/market')" />
          <SidebarItem to="/watchlist" icon="pi-star-fill" label="Watchlist" :isActive="route.path === '/watchlist'" />
        </div>

        <div class="jd-nav-section">
          <div class="jd-nav-label">Account</div>
          <SidebarItem to="/settings/exchange" icon="pi-link" label="Exchanges" :isActive="route.path === '/settings/exchange'" />
          <SidebarItem to="/settings" icon="pi-cog" label="Settings" :isActive="route.path === '/settings'" />
        </div>

      </nav>

      <!-- User profile -->
      <div class="jd-sidebar-user">
        <div class="jd-user-card" @click="router.push('/settings')">
          <div class="jd-user-avatar">
            {{ authStore.user?.name?.charAt(0)?.toUpperCase() || 'U' }}
          </div>
          <div style="flex:1; min-width:0;">
            <div class="jd-user-name truncate">{{ authStore.user?.name || 'User' }}</div>
            <div class="jd-user-role truncate">{{ (authStore.user?.role || 'user').replace('_', ' ') }}</div>
          </div>
          <i class="pi pi-angle-right" style="font-size:11px; color:var(--jd-text-faint);"></i>
        </div>
      </div>

    </aside>

    <!-- ── Main ───────────────────────────────────────────── -->
    <main class="jd-main">

      <!-- Topbar -->
      <header class="jd-topbar">
        <!-- Left: page title -->
        <div style="flex:1;">
          <div class="jd-page-title">{{ currentPageTitle }}</div>
          <div class="jd-page-desc">{{ currentPageDescription }}</div>
        </div>

        <!-- Right: tools -->
        <div style="display:flex; align-items:center; gap:10px;">

          <!-- Clock -->
          <!-- <div style="display:flex; align-items:center; gap:6px; font-size:13px; color:var(--jd-text-muted); padding-right:10px; border-right:1px solid var(--jd-border);">
            <i class="pi pi-clock" style="font-size:13px;"></i>
            <span>{{ currentTime }}</span>
          </div> -->

          <!-- Profile -->
          <div style="position:relative;">
            <button
              @click="toggleProfileMenu"
              style="display:flex; align-items:center; gap:8px; padding:4px 10px 4px 4px; border-radius:10px; border:1px solid var(--jd-border); background:rgba(99,120,170,0.06); cursor:pointer; transition:all var(--jd-trans);"
            >
              <div class="jd-user-avatar" style="width:28px; height:28px; font-size:11px; border-radius:6px;">
                {{ authStore.user?.name?.charAt(0)?.toUpperCase() || 'U' }}
              </div>
              <span style="font-size:13px; font-weight:500; color:var(--jd-text);">{{ authStore.user?.name || 'User' }}</span>
              <i class="pi pi-angle-down" style="font-size:10px; color:var(--jd-text-muted);"></i>
            </button>

            <!-- Dropdown -->
            <transition
              enter-active-class="transition ease-out duration-100"
              enter-from-class="opacity-0 scale-95"
              enter-to-class="opacity-100 scale-100"
              leave-active-class="transition ease-in duration-75"
              leave-from-class="opacity-100 scale-100"
              leave-to-class="opacity-0 scale-95"
            >
              <div v-if="showProfileMenu" class="profile-dropdown">
                <!-- User info -->
                <div style="padding:14px 16px; border-bottom:1px solid var(--jd-border);">
                  <div style="font-size:13px; font-weight:600; color:var(--jd-text);">{{ authStore.user?.name || 'User' }}</div>
                  <div style="font-size:11px; color:var(--jd-text-muted); margin-top:2px;">{{ authStore.user?.email || '' }}</div>
                </div>
                <!-- Links -->
                <div style="padding:6px;">
                  <router-link to="/settings" @click="showProfileMenu=false" class="dropdown-item">
                    <i class="pi pi-user"></i> Profile
                  </router-link>
                  <router-link to="/settings" @click="showProfileMenu=false" class="dropdown-item">
                    <i class="pi pi-cog"></i> Settings
                  </router-link>
                  <router-link to="/settings/exchange" @click="showProfileMenu=false" class="dropdown-item">
                    <i class="pi pi-key"></i> API Keys
                  </router-link>
                </div>
                <!-- Logout -->
                <div style="padding:6px; border-top:1px solid var(--jd-border);">
                  <button @click="handleLogout" class="dropdown-item dropdown-item-danger">
                    <i class="pi pi-sign-out"></i> Sign Out
                  </button>
                </div>
              </div>
            </transition>
          </div>

        </div>
      </header>

      <!-- Page content -->
      <div class="jd-content">
        <router-view />
      </div>

    </main>

    <!-- Backdrop to close dropdown -->
    <div v-if="showProfileMenu" class="fixed inset-0 z-40" @click="showProfileMenu=false"></div>

  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import SidebarItem from './SidebarItem.vue'
import dayjs from 'dayjs'
import utc from 'dayjs/plugin/utc'
import timezone from 'dayjs/plugin/timezone'

dayjs.extend(utc)
dayjs.extend(timezone)

const router = useRouter()
const route  = useRoute()
const authStore = useAuthStore()

const showProfileMenu = ref(false)
const currentTime = ref('')
let timeTimer = null

const pageTitles = {
  'Recommendations': 'Recommendations',
  'SimAccount': 'Sim Accounts',
  'Watchlist': 'Watchlist',
  'Market': 'Market Overview',
  'SymbolDetail': 'Market Detail',
  'Settings': 'Settings',
  'ExchangeSettings': 'Exchange Connections',
}

const pageDescriptions = {
  'Recommendations': 'Daily stock recommendations and why',
  'SimAccount': 'Simulation accounts & positions',
  'Watchlist': 'Track your favorite assets',
  'Market': 'Live US stock prices',
  'SymbolDetail': 'In-depth market analysis',
  'Settings': 'Account preferences',
  'ExchangeSettings': 'Connect exchanges via API',
}

const currentPageTitle       = computed(() => pageTitles[route.name]       || 'Recommendations')
const currentPageDescription = computed(() => pageDescriptions[route.name] || '')

const toggleProfileMenu = () => { showProfileMenu.value = !showProfileMenu.value }

const handleLogout = () => {
  showProfileMenu.value = false
  authStore.logout()
  router.push('/login')
}

const updateTime = () => {
  const tz = authStore.user?.timezone || 'UTC'
  currentTime.value = dayjs().tz(tz).format('HH:mm:ss')
}

onMounted(() => {
  updateTime()
  timeTimer = setInterval(updateTime, 1000)
})

onBeforeUnmount(() => {
  if (timeTimer) clearInterval(timeTimer)
})
</script>

<style scoped>
.profile-dropdown {
  position: absolute;
  right: 0;
  top: calc(100% + 8px);
  width: 220px;
  background: var(--jd-card);
  border: 1px solid var(--jd-border);
  border-radius: 12px;
  z-index: 50;
  overflow: hidden;
  box-shadow: var(--jd-shadow-modal);
}

.dropdown-item {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 9px 12px;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 500;
  color: var(--jd-text-muted);
  text-decoration: none;
  width: 100%;
  border: none;
  background: none;
  cursor: pointer;
  transition: all var(--jd-trans);
  font-family: inherit;
}

.dropdown-item:hover {
  background: rgba(59, 130, 246, 0.08);
  color: var(--jd-text);
}

.dropdown-item-danger { color: var(--jd-red); }
.dropdown-item-danger:hover {
  background: rgba(239, 68, 68, 0.08);
  color: var(--jd-red);
}

.jd-topbar {
  position: relative;
}
</style>
