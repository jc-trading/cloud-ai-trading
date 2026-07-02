<template>
  <div class="jd-page">
    <!-- Header -->
    <div>
      <router-link to="/admin" class="jd-back-link">← Back to Admin</router-link>
      <h1 class="jd-page-title" style="font-size: 28px; margin-top: 12px;">System Monitoring</h1>
      <p class="jd-page-desc">Monitor system health and performance metrics</p>
    </div>

    <!-- System Status Overview -->
    <div class="grid grid-cols-1 md:grid-cols-4 gap-4">
      <div class="jd-card jd-card-body">
        <div class="flex items-center justify-between">
          <div>
            <p class="jd-stat-label" style="margin-bottom: 8px;">API Status</p>
            <p class="text-2xl font-bold">Online</p>
          </div>
          <div class="w-3 h-3 rounded-full" style="background: var(--jd-green)"></div>
        </div>
      </div>
      <div class="jd-card jd-card-body">
        <div class="flex items-center justify-between">
          <div>
            <p class="jd-stat-label" style="margin-bottom: 8px;">Database Status</p>
            <p class="text-2xl font-bold">Healthy</p>
          </div>
          <div class="w-3 h-3 rounded-full" style="background: var(--jd-green)"></div>
        </div>
      </div>
      <div class="jd-card jd-card-body">
        <div class="flex items-center justify-between">
          <div>
            <p class="jd-stat-label" style="margin-bottom: 8px;">Cache Status</p>
            <p class="text-2xl font-bold">Active</p>
          </div>
          <div class="w-3 h-3 rounded-full" style="background: var(--jd-green)"></div>
        </div>
      </div>
      <div class="jd-card jd-card-body">
        <div class="flex items-center justify-between">
          <div>
            <p class="jd-stat-label" style="margin-bottom: 8px;">Uptime</p>
            <p class="text-2xl font-bold">99.9%</p>
          </div>
          <div class="w-3 h-3 rounded-full" style="background: var(--jd-green)"></div>
        </div>
      </div>
    </div>

    <!-- Performance Metrics -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <!-- CPU & Memory -->
      <div class="jd-card jd-card-body">
        <h2 class="jd-card-title" style="margin-bottom: 24px;">Resource Usage</h2>

        <div class="space-y-6">
          <!-- CPU -->
          <div>
            <div class="flex justify-between mb-2">
              <span style="color: var(--jd-text-muted)">CPU Usage</span>
              <span class="font-bold">45%</span>
            </div>
            <div class="pbar"><i :style="{ width: '45%' }"></i></div>
          </div>

          <!-- Memory -->
          <div>
            <div class="flex justify-between mb-2">
              <span style="color: var(--jd-text-muted)">Memory Usage</span>
              <span class="font-bold">62%</span>
            </div>
            <div class="pbar"><i :style="{ width: '62%', background: 'var(--jd-yellow)' }"></i></div>
          </div>

          <!-- Disk -->
          <div>
            <div class="flex justify-between mb-2">
              <span style="color: var(--jd-text-muted)">Disk Usage</span>
              <span class="font-bold">38%</span>
            </div>
            <div class="pbar"><i :style="{ width: '38%' }"></i></div>
          </div>

          <!-- Network -->
          <div>
            <div class="flex justify-between mb-2">
              <span style="color: var(--jd-text-muted)">Network I/O</span>
              <span class="font-bold">28%</span>
            </div>
            <div class="pbar"><i :style="{ width: '28%' }"></i></div>
          </div>
        </div>
      </div>

      <!-- Request Stats -->
      <div class="jd-card jd-card-body">
        <h2 class="jd-card-title" style="margin-bottom: 24px;">Request Statistics</h2>

        <div class="space-y-4">
          <div class="flex justify-between items-center pb-4" style="border-bottom: 1px solid var(--jd-border)">
            <span style="color: var(--jd-text-muted)">Total Requests (24h)</span>
            <span class="text-2xl font-bold">--</span>
          </div>
          <div class="flex justify-between items-center pb-4" style="border-bottom: 1px solid var(--jd-border)">
            <span style="color: var(--jd-text-muted)">Average Response Time</span>
            <span class="text-2xl font-bold">--ms</span>
          </div>
          <div class="flex justify-between items-center pb-4" style="border-bottom: 1px solid var(--jd-border)">
            <span style="color: var(--jd-text-muted)">Error Rate</span>
            <span class="text-2xl font-bold" style="color: var(--jd-green)">0%</span>
          </div>
          <div class="flex justify-between items-center">
            <span style="color: var(--jd-text-muted)">Active Connections</span>
            <span class="text-2xl font-bold">--</span>
          </div>
        </div>
      </div>
    </div>

    <!-- System Logs -->
    <div class="jd-card jd-card-body">
      <div class="flex items-center justify-between mb-6">
        <h2 class="jd-card-title">System Logs</h2>
        <button class="jd-btn jd-btn-danger jd-btn-sm">
          <i class="pi pi-trash"></i> Clear Logs
        </button>
      </div>

      <DataTable
        :columns="logColumns"
        :data="systemLogs"
        :row-key="(r) => `${r.timestamp}-${r.message}`"
        :searchable="['message', 'source']"
        search-placeholder="Search logs…"
        :page-size="10"
        empty-text="No system logs available"
      >
        <template #cell:level="{ value }">
          <span class="jd-badge" :class="getLogColor(value)">{{ value }}</span>
        </template>
      </DataTable>
    </div>

    <!-- Database Info -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <div class="jd-card jd-card-body">
        <h2 class="jd-card-title" style="margin-bottom: 24px;">Database Status</h2>

        <div class="space-y-4">
          <div>
            <p class="text-sm mb-1" style="color: var(--jd-text-muted)">Database Type</p>
            <p class="font-semibold">PostgreSQL</p>
          </div>
          <div>
            <p class="text-sm mb-1" style="color: var(--jd-text-muted)">Connection Pool</p>
            <p class="font-semibold">-- / 20</p>
          </div>
          <div>
            <p class="text-sm mb-1" style="color: var(--jd-text-muted)">Total Queries (24h)</p>
            <p class="font-semibold">--</p>
          </div>
          <div>
            <p class="text-sm mb-1" style="color: var(--jd-text-muted)">Slow Queries</p>
            <p class="font-semibold">0</p>
          </div>
        </div>
      </div>

      <div class="jd-card jd-card-body">
        <h2 class="jd-card-title" style="margin-bottom: 24px;">Cache Status</h2>

        <div class="space-y-4">
          <div>
            <p class="text-sm mb-1" style="color: var(--jd-text-muted)">Cache Type</p>
            <p class="font-semibold">Redis</p>
          </div>
          <div>
            <p class="text-sm mb-1" style="color: var(--jd-text-muted)">Memory Usage</p>
            <p class="font-semibold">--MB / 512MB</p>
          </div>
          <div>
            <p class="text-sm mb-1" style="color: var(--jd-text-muted)">Hit Rate</p>
            <p class="font-semibold">-- %</p>
          </div>
          <div>
            <p class="text-sm mb-1" style="color: var(--jd-text-muted)">Keys Stored</p>
            <p class="font-semibold">--</p>
          </div>
        </div>
      </div>
    </div>

    <!-- Configuration -->
    <div class="jd-card jd-card-body">
      <h2 class="jd-card-title" style="margin-bottom: 24px;">System Configuration</h2>

      <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div>
          <p class="text-sm mb-1" style="color: var(--jd-text-muted)">Version</p>
          <p class="font-semibold">1.0.0</p>
        </div>
        <div>
          <p class="text-sm mb-1" style="color: var(--jd-text-muted)">Environment</p>
          <p class="font-semibold">Production</p>
        </div>
        <div>
          <p class="text-sm mb-1" style="color: var(--jd-text-muted)">Node Count</p>
          <p class="font-semibold">--</p>
        </div>
        <div>
          <p class="text-sm mb-1" style="color: var(--jd-text-muted)">API Rate Limit</p>
          <p class="font-semibold">1000 req/min</p>
        </div>
      </div>

      <div class="mt-6 pt-6" style="border-top: 1px solid var(--jd-border)">
        <button class="jd-btn jd-btn-warning">
          <i class="pi pi-refresh"></i> Restart Services
        </button>
        <button class="jd-btn jd-btn-ghost ml-2">
          <i class="pi pi-file"></i> View Configuration
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import DataTable from '@/components/common/DataTable.vue'

const systemLogs = ref([])

const logColumns = [
  { key: 'timestamp', header: 'Time', sortable: true },
  { key: 'level', header: 'Level', align: 'center', filterable: true, filterLabel: 'Level' },
  { key: 'message', header: 'Message' },
  { key: 'source', header: 'Source', sortable: true, filterable: true, filterLabel: 'Source' },
]

const getLogColor = (level) => {
  switch (level) {
    case 'ERROR':
      return 'red'
    case 'WARNING':
      return 'yellow'
    case 'INFO':
      return 'cyan'
    default:
      return 'green'
  }
}
</script>

<style scoped>
.jd-back-link { color: var(--jd-cyan); font-size: 13px; text-decoration: none; }
.jd-back-link:hover { color: var(--jd-text); }

.pbar { height: 8px; background: var(--jd-border); border-radius: 4px; overflow: hidden; }
.pbar i { display: block; height: 100%; background: var(--jd-cyan); border-radius: 4px; }

a {
  text-decoration: none;
}
</style>
