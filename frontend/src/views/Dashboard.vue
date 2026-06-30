<template>
  <div class="jd-page">
    <!-- Stat Cards Grid -->
    <div class="stats-grid">
      <!-- System Status -->
      <div class="jd-stat-card jd-stat-card-green">
        <div class="jd-stat-content">
          <div class="jd-stat-label">System Status</div>
          <div class="jd-stat-value">Healthy</div>
          <div class="jd-stat-sub">All systems operational</div>
        </div>
        <div class="jd-stat-icon green">
          <i class="pi pi-check-circle"></i>
        </div>
      </div>

      <!-- Uptime -->
      <div class="jd-stat-card jd-stat-card-blue">
        <div class="jd-stat-content">
          <div class="jd-stat-label">Uptime</div>
          <div class="jd-stat-value">{{ uptime }}%</div>
          <div class="jd-stat-sub">Last 30 days</div>
        </div>
        <div class="jd-stat-icon blue">
          <i class="pi pi-clock"></i>
        </div>
      </div>

      <!-- Active Tasks -->
      <div class="jd-stat-card jd-stat-card-yellow">
        <div class="jd-stat-content">
          <div class="jd-stat-label">Active Tasks</div>
          <div class="jd-stat-value">{{ activeTaskCount }}</div>
          <div class="jd-stat-sub">Running now</div>
        </div>
        <div class="jd-stat-icon yellow">
          <i class="pi pi-list-check"></i>
        </div>
      </div>

      <!-- Last Update -->
      <div class="jd-stat-card jd-stat-card-purple">
        <div class="jd-stat-content">
          <div class="jd-stat-label">Last Update</div>
          <div class="jd-stat-value">{{ lastUpdate }}</div>
          <div class="jd-stat-sub">Auto-refreshing</div>
        </div>
        <div class="jd-stat-icon purple">
          <i class="pi pi-refresh"></i>
        </div>
      </div>
    </div>

    <!-- Main Content Grid -->
    <div class="main-grid">
      <!-- System Health Card -->
      <div class="jd-card system-health-card">
        <div class="jd-card-header">
          <h3 class="jd-card-title">System Health</h3>
          <i class="pi pi-heart"></i>
        </div>
        <div class="jd-card-body">
          <SystemMonitor :metrics="metrics" :health="health" :isDarkMode="true" />
        </div>
      </div>

      <!-- Task Status Card -->
      <div class="jd-card task-status-card">
        <div class="jd-card-header">
          <h3 class="jd-card-title">Task Health Status</h3>
          <button
            @click="refreshAll"
            :disabled="refreshing"
            class="jd-btn jd-btn-primary jd-btn-sm refresh-btn"
            :class="{ 'is-spinning': refreshing }"
          >
            <i class="pi pi-refresh"></i>
            <span>{{ refreshing ? 'Refreshing...' : 'Refresh' }}</span>
          </button>
        </div>
        <div class="jd-card-body">
          <TaskStatusPanel :tasks="taskStatus" :isDarkMode="true" />
        </div>
      </div>
    </div>

    <!-- System Logs Card -->
    <div class="jd-card logs-card">
      <div class="jd-card-header">
        <div class="logs-header-left">
          <h3 class="jd-card-title">System Logs</h3>
          <div class="jd-live-dot"></div>
          <span class="live-label">LIVE</span>
        </div>
        <div class="logs-header-right">
          <label for="log-filter" class="filter-label">Filter:</label>
          <select
            id="log-filter"
            @change="handleFilterChange({ level: $event.target.value })"
            class="jd-input jd-select"
          >
            <option value="">All Levels</option>
            <option value="INFO">Info</option>
            <option value="WARNING">Warning</option>
            <option value="ERROR">Error</option>
          </select>
        </div>
      </div>
      <div class="jd-card-body">
        <LogViewer :logs="logs" :isDarkMode="true" @filter-change="handleFilterChange" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { getMetrics, getLogs, getTaskStatus, getHealth } from '@/api'
import { wsManager } from '@/utils/websocket'
import SystemMonitor from '@/components/SystemMonitor.vue'
import LogViewer from '@/components/LogViewer.vue'
import TaskStatusPanel from '@/components/TaskStatusPanel.vue'

const metrics = ref(null)
const logs = ref([])
const taskStatus = ref([])
const health = ref(null)
const refreshing = ref(false)
const metricsInterval = ref(null)
const logFilter = ref({})
const uptime = ref(99.9)
const lastUpdate = ref('Now')
let timeTimer = null

const activeTaskCount = computed(() => {
  return taskStatus.value?.filter(t => t.status === 'online').length || 0
})

const refreshAll = async () => {
  refreshing.value = true
  try {
    await Promise.all([
      refreshMetrics(),
      refreshLogs(),
      refreshTaskStatus()
    ])
    lastUpdate.value = 'Just now'
  } catch (error) {
    console.error('Error refreshing:', error)
  } finally {
    refreshing.value = false
  }
}

const refreshMetrics = async () => {
  try {
    const response = await getMetrics()
    metrics.value = response.data

    const healthResponse = await getHealth()
    health.value = healthResponse.data
  } catch (error) {
    console.error('Failed to fetch metrics:', error)
  }
}

const refreshLogs = async () => {
  try {
    const response = await getLogs(logFilter.value.category, logFilter.value.level)
    logs.value = response.data.logs || []
  } catch (error) {
    console.error('Failed to fetch logs:', error)
  }
}

const refreshTaskStatus = async () => {
  try {
    const response = await getTaskStatus()
    taskStatus.value = response.data?.tasks || []
  } catch (error) {
    console.error('Failed to fetch task status:', error)
  }
}

const handleFilterChange = (filter) => {
  logFilter.value = filter
  refreshLogs()
}

const setupWebSocket = async () => {
  try {
    await wsManager.connect()
    wsManager.on('message', (data) => {
      if (data.type === 'log' && logs.value) {
        logs.value.unshift(data.data)
        if (logs.value.length > 100) {
          logs.value.pop()
        }
      }
    })
  } catch (error) {
    console.warn('WebSocket connection failed, will use polling:', error)
  }
}

const updateLastUpdate = () => {
  const now = new Date()
  const hours = now.getHours()
  const minutes = now.getMinutes()
  const ampm = hours >= 12 ? 'PM' : 'AM'
  const displayHours = hours % 12 || 12
  lastUpdate.value = `${displayHours}:${minutes.toString().padStart(2, '0')} ${ampm}`
}

onMounted(async () => {
  await refreshAll()

  metricsInterval.value = setInterval(refreshMetrics, 5000)
  timeTimer = setInterval(updateLastUpdate, 60000)
  updateLastUpdate()

  await setupWebSocket()
})

onBeforeUnmount(() => {
  if (metricsInterval.value) clearInterval(metricsInterval.value)
  if (timeTimer) clearInterval(timeTimer)
  wsManager.disconnect()
})
</script>

<style scoped>
/* Stats Grid Layout */
.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
  gap: 20px;
}

/* Main Content Grid Layout */
.main-grid {
  display: grid;
  grid-template-columns: 1fr 2fr;
  gap: 20px;
}

/* System Health Card - spans 1 column */
.system-health-card {
  min-width: 0;
}

/* Task Status Card - spans 2 columns */
.task-status-card {
  min-width: 0;
}

/* Logs Card - full width */
.logs-card {
  grid-column: 1 / -1;
}

/* Refresh Button Styling */
.refresh-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.875rem;
  padding: 6px 12px;
  white-space: nowrap;
}

.refresh-btn i {
  font-size: 0.875rem;
}

.refresh-btn.is-spinning i {
  animation: spin-icon 1s linear infinite;
}

/* Logs Header Layout */
.logs-header-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.logs-header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.filter-label {
  font-size: 0.875rem;
  color: var(--jd-text-muted);
  font-weight: 500;
}

/* Live Label and Dot */
.live-label {
  font-size: 0.75rem;
  font-weight: 600;
  letter-spacing: 0.5px;
  color: var(--jd-green);
}

/* Spin Animation for Icon */
@keyframes spin-icon {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

/* Responsive Grid for smaller screens */
@media (max-width: 1024px) {
  .main-grid {
    grid-template-columns: 1fr;
  }

  .task-status-card {
    grid-column: 1;
  }

  .logs-card {
    grid-column: 1;
  }
}

@media (max-width: 768px) {
  .stats-grid {
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 16px;
  }

  .logs-header-left,
  .logs-header-right {
    flex-direction: column;
    align-items: flex-start;
    gap: 6px;
  }

  .logs-header-right {
    width: 100%;
  }

  .jd-select {
    width: 100%;
  }
}
</style>
