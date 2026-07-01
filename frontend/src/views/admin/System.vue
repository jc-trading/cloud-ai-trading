<template>
  <div class="min-h-screen bg-gray-950 text-white p-6">
    <div class="max-w-7xl mx-auto">
      <!-- Header -->
      <div class="mb-8">
        <router-link to="/admin" class="text-blue-400 hover:text-blue-300 mb-4 inline-block">← Back to Admin</router-link>
        <h1 class="text-4xl font-bold text-white mb-2">System Monitoring</h1>
        <p class="text-gray-400">Monitor system health and performance metrics</p>
      </div>

      <!-- System Status Overview -->
      <div class="grid grid-cols-1 md:grid-cols-4 gap-4 mb-8">
        <div class="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <div class="flex items-center justify-between">
            <div>
              <p class="text-gray-400 text-sm mb-2">API Status</p>
              <p class="text-2xl font-bold">Online</p>
            </div>
            <div class="w-3 h-3 bg-green-500 rounded-full"></div>
          </div>
        </div>
        <div class="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <div class="flex items-center justify-between">
            <div>
              <p class="text-gray-400 text-sm mb-2">Database Status</p>
              <p class="text-2xl font-bold">Healthy</p>
            </div>
            <div class="w-3 h-3 bg-green-500 rounded-full"></div>
          </div>
        </div>
        <div class="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <div class="flex items-center justify-between">
            <div>
              <p class="text-gray-400 text-sm mb-2">Cache Status</p>
              <p class="text-2xl font-bold">Active</p>
            </div>
            <div class="w-3 h-3 bg-green-500 rounded-full"></div>
          </div>
        </div>
        <div class="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <div class="flex items-center justify-between">
            <div>
              <p class="text-gray-400 text-sm mb-2">Uptime</p>
              <p class="text-2xl font-bold">99.9%</p>
            </div>
            <div class="w-3 h-3 bg-green-500 rounded-full"></div>
          </div>
        </div>
      </div>

      <!-- Performance Metrics -->
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
        <!-- CPU & Memory -->
        <div class="bg-gray-900 border border-gray-800 rounded-lg p-6">
          <h2 class="text-xl font-bold text-white mb-6">Resource Usage</h2>

          <div class="space-y-6">
            <!-- CPU -->
            <div>
              <div class="flex justify-between mb-2">
                <span class="text-gray-400">CPU Usage</span>
                <span class="text-white font-bold">45%</span>
              </div>
              <ProgressBar :value="45" class="h-2"></ProgressBar>
            </div>

            <!-- Memory -->
            <div>
              <div class="flex justify-between mb-2">
                <span class="text-gray-400">Memory Usage</span>
                <span class="text-white font-bold">62%</span>
              </div>
              <ProgressBar :value="62" class="h-2"></ProgressBar>
            </div>

            <!-- Disk -->
            <div>
              <div class="flex justify-between mb-2">
                <span class="text-gray-400">Disk Usage</span>
                <span class="text-white font-bold">38%</span>
              </div>
              <ProgressBar :value="38" class="h-2"></ProgressBar>
            </div>

            <!-- Network -->
            <div>
              <div class="flex justify-between mb-2">
                <span class="text-gray-400">Network I/O</span>
                <span class="text-white font-bold">28%</span>
              </div>
              <ProgressBar :value="28" class="h-2"></ProgressBar>
            </div>
          </div>
        </div>

        <!-- Request Stats -->
        <div class="bg-gray-900 border border-gray-800 rounded-lg p-6">
          <h2 class="text-xl font-bold text-white mb-6">Request Statistics</h2>

          <div class="space-y-4">
            <div class="flex justify-between items-center border-b border-gray-700 pb-4">
              <span class="text-gray-400">Total Requests (24h)</span>
              <span class="text-2xl font-bold">--</span>
            </div>
            <div class="flex justify-between items-center border-b border-gray-700 pb-4">
              <span class="text-gray-400">Average Response Time</span>
              <span class="text-2xl font-bold">--ms</span>
            </div>
            <div class="flex justify-between items-center border-b border-gray-700 pb-4">
              <span class="text-gray-400">Error Rate</span>
              <span class="text-2xl font-bold text-green-400">0%</span>
            </div>
            <div class="flex justify-between items-center">
              <span class="text-gray-400">Active Connections</span>
              <span class="text-2xl font-bold">--</span>
            </div>
          </div>
        </div>
      </div>

      <!-- System Logs -->
      <div class="bg-gray-900 border border-gray-800 rounded-lg p-6 mb-8">
        <div class="flex items-center justify-between mb-6">
          <h2 class="text-xl font-bold text-white">System Logs</h2>
          <Button label="Clear Logs" icon="pi pi-trash" class="p-button-outlined p-button-danger"></Button>
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
            <Tag :value="value" :severity="getLogSeverity(value)" />
          </template>
        </DataTable>
      </div>

      <!-- Database Info -->
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
        <div class="bg-gray-900 border border-gray-800 rounded-lg p-6">
          <h2 class="text-xl font-bold text-white mb-6">Database Status</h2>

          <div class="space-y-4">
            <div>
              <p class="text-gray-400 text-sm mb-1">Database Type</p>
              <p class="text-white font-semibold">PostgreSQL</p>
            </div>
            <div>
              <p class="text-gray-400 text-sm mb-1">Connection Pool</p>
              <p class="text-white font-semibold">-- / 20</p>
            </div>
            <div>
              <p class="text-gray-400 text-sm mb-1">Total Queries (24h)</p>
              <p class="text-white font-semibold">--</p>
            </div>
            <div>
              <p class="text-gray-400 text-sm mb-1">Slow Queries</p>
              <p class="text-white font-semibold">0</p>
            </div>
          </div>
        </div>

        <div class="bg-gray-900 border border-gray-800 rounded-lg p-6">
          <h2 class="text-xl font-bold text-white mb-6">Cache Status</h2>

          <div class="space-y-4">
            <div>
              <p class="text-gray-400 text-sm mb-1">Cache Type</p>
              <p class="text-white font-semibold">Redis</p>
            </div>
            <div>
              <p class="text-gray-400 text-sm mb-1">Memory Usage</p>
              <p class="text-white font-semibold">--MB / 512MB</p>
            </div>
            <div>
              <p class="text-gray-400 text-sm mb-1">Hit Rate</p>
              <p class="text-white font-semibold">-- %</p>
            </div>
            <div>
              <p class="text-gray-400 text-sm mb-1">Keys Stored</p>
              <p class="text-white font-semibold">--</p>
            </div>
          </div>
        </div>
      </div>

      <!-- Configuration -->
      <div class="bg-gray-900 border border-gray-800 rounded-lg p-6">
        <h2 class="text-xl font-bold text-white mb-6">System Configuration</h2>

        <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div>
            <p class="text-gray-400 text-sm mb-1">Version</p>
            <p class="text-white font-semibold">1.0.0</p>
          </div>
          <div>
            <p class="text-gray-400 text-sm mb-1">Environment</p>
            <p class="text-white font-semibold">Production</p>
          </div>
          <div>
            <p class="text-gray-400 text-sm mb-1">Node Count</p>
            <p class="text-white font-semibold">--</p>
          </div>
          <div>
            <p class="text-gray-400 text-sm mb-1">API Rate Limit</p>
            <p class="text-white font-semibold">1000 req/min</p>
          </div>
        </div>

        <div class="mt-6 pt-6 border-t border-gray-700">
          <Button label="Restart Services" icon="pi pi-refresh" class="p-button-warning"></Button>
          <Button label="View Configuration" icon="pi pi-file" class="ml-2 p-button-outlined"></Button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import DataTable from '@/components/common/DataTable.vue'
import Button from 'primevue/button'
import Tag from 'primevue/tag'
import ProgressBar from 'primevue/progressbar'

const systemLogs = ref([])

const logColumns = [
  { key: 'timestamp', header: 'Time', sortable: true },
  { key: 'level', header: 'Level', align: 'center', filterable: true, filterLabel: 'Level' },
  { key: 'message', header: 'Message' },
  { key: 'source', header: 'Source', sortable: true, filterable: true, filterLabel: 'Source' },
]

const getLogSeverity = (level) => {
  switch (level) {
    case 'ERROR':
      return 'danger'
    case 'WARNING':
      return 'warning'
    case 'INFO':
      return 'info'
    default:
      return 'success'
  }
}
</script>

<style scoped>
:deep(.p-progressbar) {
  background-color: #374151;
}

:deep(.p-progressbar .p-progressbar-value) {
  background-color: #3b82f6;
}

:deep(.p-button) {
  background-color: #3b82f6;
  border-color: #3b82f6;
}

:deep(.p-button:hover) {
  background-color: #2563eb;
}

:deep(.p-button-warning) {
  background-color: #f59e0b;
  border-color: #f59e0b;
}

:deep(.p-button-warning:hover) {
  background-color: #d97706;
}

a {
  text-decoration: none;
}
</style>
