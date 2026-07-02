<template>
  <div class="jd-page">
    <!-- Stats Cards -->
    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
      <div class="jd-stat-card blue">
        <div class="jd-stat-icon blue">
          <i class="pi pi-bell"></i>
        </div>
        <div class="jd-stat-label">Total Signals</div>
        <div class="jd-stat-value">{{ stats.totalSignals }}</div>
        <div class="jd-stat-sub">All time signals</div>
      </div>

      <div class="jd-stat-card green">
        <div class="jd-stat-icon green">
          <i class="pi pi-arrow-up"></i>
        </div>
        <div class="jd-stat-label">Buy Signals</div>
        <div class="jd-stat-value">{{ stats.buySignals }}</div>
        <div class="jd-stat-sub">{{ buyPercentage }}% of signals</div>
      </div>

      <div class="jd-stat-card red">
        <div class="jd-stat-icon red">
          <i class="pi pi-arrow-down"></i>
        </div>
        <div class="jd-stat-label">Sell Signals</div>
        <div class="jd-stat-value">{{ stats.sellSignals }}</div>
        <div class="jd-stat-sub">{{ sellPercentage }}% of signals</div>
      </div>

      <div class="jd-stat-card yellow">
        <div class="jd-stat-icon yellow">
          <i class="pi pi-star"></i>
        </div>
        <div class="jd-stat-label">Strong Signals</div>
        <div class="jd-stat-value">{{ stats.strongSignals }}</div>
        <div class="jd-stat-sub">Signal strength > 0.7</div>
      </div>
    </div>

    <!-- Signals Table -->
    <DataTable
      :columns="signalColumns"
      :data="signals"
      :searchable="['symbol']"
      search-placeholder="Filter by symbol (e.g., BTCUSDT)"
      :page-size="10"
      clickable-rows
      empty-text="No signals found"
      @row-click="selectedSignal = $event"
    >
      <template #toolbar-right>
        <button
          @click="refreshSignals"
          :disabled="loading"
          class="jd-btn jd-btn-primary jd-btn-sm flex items-center gap-2"
        >
          <i :class="['pi', 'pi-refresh', { 'animate-spin': loading }]"></i>
          {{ loading ? 'Loading...' : 'Refresh' }}
        </button>
      </template>

      <template #cell:type="{ row }">
        <span :class="['jd-badge', normalizedSignalType(row) === 'buy' ? 'green' : 'red']">
          {{ displaySignalType(row) }}
        </span>
      </template>

      <template #cell:strength="{ row }">
        <div style="display: flex; align-items: center; justify-content: flex-end; gap: 8px">
          <div style="width: 96px; background: rgba(75, 85, 99, 0.5); border-radius: 9999px; height: 8px">
            <div
              :style="{ width: signalStrengthPercent(row) + '%' }"
              :class="getStrengthColor(row)"
              style="height: 100%; border-radius: 9999px"
            ></div>
          </div>
          <span style="font-weight: 600; color: var(--jd-text); width: 48px; text-align: right">
            {{ signalStrengthPercent(row).toFixed(0) }}%
          </span>
        </div>
      </template>

      <template #cell:strategy="{ row }">
        <span style="color: var(--jd-text-muted); display: flex; align-items: center; gap: 4px">
          <i
            :class="{
              'pi pi-arrow-up': normalizedStrategy(row) === 'momentum',
              'pi pi-arrow-down': normalizedStrategy(row) === 'contrarian',
            }"
            :style="{
              color: normalizedStrategy(row) === 'momentum' ? 'var(--jd-green)' : normalizedStrategy(row) === 'contrarian' ? 'var(--jd-red)' : 'inherit'
            }"
          ></i>
          {{ row.strategy || '-' }}
        </span>
      </template>

      <template #cell:time="{ value }">
        <span style="color: var(--jd-text-muted); font-size: 0.875rem">{{ formatTime(value) }}</span>
      </template>
    </DataTable>

    <!-- Signal Details (Optional) -->
    <div v-if="selectedSignal" class="jd-card">
      <div class="jd-card-header">
        <h3 class="jd-card-title">Signal Details</h3>
        <button
          @click="selectedSignal = null"
          style="background: none; border: none; color: var(--jd-text-muted); cursor: pointer; padding: 4px; font-size: 1.25rem"
        >
          <i class="pi pi-times"></i>
        </button>
      </div>
      <div class="jd-card-body">
        <div class="grid grid-cols-2 gap-6">
          <div>
            <p style="color: var(--jd-text-muted); font-size: 0.875rem; margin-bottom: 8px">Symbol</p>
            <p style="font-size: 1.25rem; font-weight: bold; color: var(--jd-text)">{{ selectedSignal.symbol }}</p>
          </div>
          <div>
            <p style="color: var(--jd-text-muted); font-size: 0.875rem; margin-bottom: 8px">Signal Type</p>
            <p style="font-size: 1.25rem; font-weight: bold" :class="normalizedSignalType(selectedSignal) === 'buy' ? 'price-up' : 'price-down'">
              {{ displaySignalType(selectedSignal) }}
            </p>
          </div>
          <div>
            <p style="color: var(--jd-text-muted); font-size: 0.875rem; margin-bottom: 8px">Strength</p>
            <p style="font-size: 1.25rem; font-weight: bold; color: var(--jd-blue)">{{ signalStrengthPercent(selectedSignal).toFixed(1) }}%</p>
          </div>
          <div>
            <p style="color: var(--jd-text-muted); font-size: 0.875rem; margin-bottom: 8px">Strategy</p>
            <p style="font-size: 1.25rem; font-weight: bold; color: var(--jd-text)">{{ selectedSignal.strategy || '-' }}</p>
          </div>
          <div style="grid-column: span 2">
            <p style="color: var(--jd-text-muted); font-size: 0.875rem; margin-bottom: 8px">Indicators</p>
            <pre style="background: rgba(0, 0, 0, 0.2); padding: 16px; border-radius: 4px; font-size: 0.75rem; color: var(--jd-text-muted); overflow-x: auto">{{ JSON.stringify(selectedSignal.indicators_used || {}, null, 2) }}</pre>
          </div>
        </div>
      </div>
    </div>

    <!-- Loading State -->
    <div v-if="loading" style="display: flex; align-items: center; justify-content: center; padding: 48px 16px">
      <div style="text-align: center">
        <i class="pi pi-spin pi-spinner" style="color: var(--jd-blue); font-size: 2rem; display: inline-block; margin-bottom: 16px"></i>
        <p style="color: var(--jd-text-muted)">Loading signals...</p>
      </div>
    </div>

    <!-- Error State -->
    <div v-if="error" class="jd-alert error">
      <p style="font-weight: 600; margin-bottom: 8px">Error Loading Signals</p>
      <p style="font-size: 0.875rem; margin-bottom: 16px">{{ error }}</p>
      <button
        @click="refreshSignals"
        class="jd-btn jd-btn-danger jd-btn-sm"
      >
        Try Again
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { getSignals } from '@/api/trading'
import DataTable from '@/components/common/DataTable.vue'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'

dayjs.extend(relativeTime)

const signals = ref([])
const loading = ref(false)
const error = ref(null)
const selectedSignal = ref(null)
let refreshInterval = null

const signalColumns = [
  { key: 'symbol', header: 'Symbol', sortable: true },
  {
    key: 'type',
    header: 'Type',
    accessor: (r) => normalizedSignalType(r),
    filterable: true,
    filterLabel: 'Type',
    filterOptions: [
      { label: 'Buy', value: 'buy' },
      { label: 'Sell', value: 'sell' },
    ],
  },
  { key: 'signal', header: 'Signal', accessor: (r) => r.recommendation || displaySignalType(r) },
  {
    key: 'strength',
    header: 'Strength',
    align: 'right',
    accessor: (r) => strengthBucket(r),
    filterable: true,
    filterLabel: 'Strength',
    filterOptions: [
      { label: 'Strong (>0.7)', value: 'strong' },
      { label: 'Medium (0.5-0.7)', value: 'medium' },
      { label: 'Weak (<0.5)', value: 'weak' },
    ],
  },
  {
    key: 'strategy',
    header: 'Strategy',
    accessor: (r) => r.strategy || '',
    filterable: true,
    filterLabel: 'Strategy',
  },
  { key: 'time', header: 'Time', accessor: (r) => r.created_at },
]

const stats = computed(() => {
  const total = signals.value.length
  const buy = signals.value.filter(s => normalizedSignalType(s) === 'buy').length
  const sell = signals.value.filter(s => normalizedSignalType(s) === 'sell').length
  const strong = signals.value.filter(s => signalStrengthPercent(s) > 70).length

  return {
    totalSignals: total,
    buySignals: buy,
    sellSignals: sell,
    strongSignals: strong,
  }
})

const buyPercentage = computed(() => {
  if (stats.value.totalSignals === 0) return 0
  return Math.round((stats.value.buySignals / stats.value.totalSignals) * 100)
})

const sellPercentage = computed(() => {
  if (stats.value.totalSignals === 0) return 0
  return Math.round((stats.value.sellSignals / stats.value.totalSignals) * 100)
})

const formatTime = (timestamp) => {
  return dayjs(timestamp).fromNow()
}

const normalizedSignalType = (signal) => {
  const type = String(signal?.signal_type || '').toLowerCase()
  if (type.includes('buy')) return 'buy'
  if (type.includes('sell')) return 'sell'
  return type
}

const displaySignalType = (signal) => {
  return String(signal?.signal_type || '-').replace('_', ' ').toUpperCase()
}

const normalizedStrategy = (signal) => {
  return String(signal?.strategy || '').toLowerCase()
}

const signalStrengthPercent = (signal) => {
  const raw = Number(signal?.signal_strength ?? signal?.confidence ?? 0)
  return raw <= 1 ? raw * 100 : raw
}

const strengthBucket = (signal) => {
  const strength = signalStrengthPercent(signal)
  if (strength > 70) return 'strong'
  if (strength > 50) return 'medium'
  return 'weak'
}

const getStrengthColor = (signal) => {
  const strength = signalStrengthPercent(signal)
  if (strength > 70) return 'strength-high'
  if (strength > 50) return 'strength-medium'
  return 'strength-low'
}

const refreshSignals = async () => {
  loading.value = true
  error.value = null
  try {
    const response = await getSignals(100)
    signals.value = response.data || []
  } catch (err) {
    error.value = err.response?.data?.detail || 'Failed to load signals'
    console.error('Error loading signals:', err)
  } finally {
    loading.value = false
  }
}

const setupAutoRefresh = () => {
  // Refresh signals every 10 seconds
  refreshInterval = setInterval(() => {
    refreshSignals()
  }, 10000)
}

onMounted(() => {
  refreshSignals()
  setupAutoRefresh()
})

onBeforeUnmount(() => {
  if (refreshInterval) clearInterval(refreshInterval)
})
</script>

<style scoped>
.animate-spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

:deep(.pi-spin) {
  display: inline-block;
  animation: spin 1s linear infinite;
}

.price-up {
  color: var(--jd-green);
}

.price-down {
  color: var(--jd-red);
}

.jd-badge {
  display: inline-block;
  padding: 4px 8px;
  border-radius: 12px;
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
}

.jd-badge.green {
  background-color: rgba(34, 197, 94, 0.2);
  color: var(--jd-green);
}

.jd-badge.red {
  background-color: rgba(239, 68, 68, 0.2);
  color: var(--jd-red);
}

.jd-badge.blue {
  background-color: rgba(59, 130, 246, 0.2);
  color: var(--jd-blue);
}

.jd-badge.yellow {
  background-color: rgba(234, 179, 8, 0.2);
  color: var(--jd-yellow);
}

.jd-badge.gray {
  background-color: rgba(107, 114, 128, 0.2);
  color: var(--jd-text-muted);
}

.strength-high {
  background-color: var(--jd-green);
}

.strength-medium {
  background-color: var(--jd-yellow);
}

.strength-low {
  background-color: var(--jd-red);
}
</style>
