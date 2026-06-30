<template>
  <div class="jd-page">
    <!-- Toolbar -->
    <div class="flex justify-end">
      <button
        @click="refreshSignals"
        :disabled="loading"
        class="jd-btn jd-btn-primary jd-btn-sm flex items-center gap-2"
      >
        <i :class="['pi', 'pi-refresh', { 'animate-spin': loading }]"></i>
        {{ loading ? 'Loading...' : 'Refresh' }}
      </button>
    </div>

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

    <!-- Filters -->
    <div class="jd-card">
      <div class="jd-card-body">
        <div class="flex items-center gap-4 flex-wrap">
          <div class="flex-1 min-w-64">
            <input
              v-model="filters.symbol"
              type="text"
              placeholder="Filter by symbol (e.g., BTCUSDT)"
              style="width: 100%; padding: 8px 12px; background: rgba(75, 85, 99, 0.5); border: 1px solid var(--jd-border); border-radius: 4px; color: var(--jd-text); outline: none"
            />
          </div>
          <div>
            <select
              v-model="filters.type"
              style="padding: 8px 12px; background: rgba(75, 85, 99, 0.5); border: 1px solid var(--jd-border); border-radius: 4px; color: var(--jd-text); outline: none"
            >
              <option value="">All Types</option>
              <option value="buy">Buy</option>
              <option value="sell">Sell</option>
              <option value="momentum">Momentum</option>
              <option value="contrarian">Contrarian</option>
            </select>
          </div>
          <div>
            <select
              v-model="filters.strength"
              style="padding: 8px 12px; background: rgba(75, 85, 99, 0.5); border: 1px solid var(--jd-border); border-radius: 4px; color: var(--jd-text); outline: none"
            >
              <option value="">All Strengths</option>
              <option value="strong">Strong (>0.7)</option>
              <option value="medium">Medium (0.5-0.7)</option>
              <option value="weak">Weak (<0.5)</option>
            </select>
          </div>
        </div>
      </div>
    </div>

    <!-- Signals Table -->
    <div class="jd-card">
      <div class="overflow-x-auto">
        <table class="jd-table">
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Type</th>
              <th>Signal</th>
              <th style="text-align: right">Strength</th>
              <th>Strategy</th>
              <th>Time</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="signal in filteredSignals"
              :key="signal.id"
              @click="selectedSignal = signal"
              class="signal-row"
            >
              <td>{{ signal.symbol }}</td>
              <td>
                <span :class="['jd-badge', normalizedSignalType(signal) === 'buy' ? 'green' : 'red']">
                  {{ displaySignalType(signal) }}
                </span>
              </td>
              <td>{{ signal.recommendation || displaySignalType(signal) }}</td>
              <td style="text-align: right">
                <div style="display: flex; align-items: center; justify-content: flex-end; gap: 8px">
                  <div style="width: 96px; background: rgba(75, 85, 99, 0.5); border-radius: 9999px; height: 8px">
                    <div
                      :style="{ width: signalStrengthPercent(signal) + '%' }"
                      :class="getStrengthColor(signal)"
                      style="height: 100%; border-radius: 9999px"
                    ></div>
                  </div>
                  <span style="font-weight: 600; color: var(--jd-text); width: 48px; text-align: right">
                    {{ signalStrengthPercent(signal).toFixed(0) }}%
                  </span>
                </div>
              </td>
              <td>
                <span style="color: var(--jd-text-muted); display: flex; align-items: center; gap: 4px">
                  <i
                    :class="{
                      'pi pi-arrow-up': normalizedStrategy(signal) === 'momentum',
                      'pi pi-arrow-down': normalizedStrategy(signal) === 'contrarian',
                    }"
                    :style="{
                      color: normalizedStrategy(signal) === 'momentum' ? 'var(--jd-green)' : normalizedStrategy(signal) === 'contrarian' ? 'var(--jd-red)' : 'inherit'
                    }"
                  ></i>
                  {{ signal.strategy || '-' }}
                </span>
              </td>
              <td style="color: var(--jd-text-muted); font-size: 0.875rem">
                {{ formatTime(signal.created_at) }}
              </td>
            </tr>
            <tr v-if="filteredSignals.length === 0">
              <td colspan="6" style="text-align: center; padding: 32px 16px">
                <span style="color: var(--jd-text-muted)">No signals found</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

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
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'

dayjs.extend(relativeTime)

const signals = ref([])
const loading = ref(false)
const error = ref(null)
const selectedSignal = ref(null)
let refreshInterval = null

const filters = ref({
  symbol: '',
  type: '',
  strength: '',
})

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

const filteredSignals = computed(() => {
  return signals.value.filter((signal) => {
    if (filters.value.symbol && !signal.symbol.includes(filters.value.symbol.toUpperCase())) {
      return false
    }
    if (filters.value.type && normalizedSignalType(signal) !== filters.value.type) {
      return false
    }
    if (filters.value.strength) {
      const strength = signalStrengthPercent(signal)
      if (filters.value.strength === 'strong' && strength <= 70) return false
      if (filters.value.strength === 'medium' && (strength <= 50 || strength > 70)) return false
      if (filters.value.strength === 'weak' && strength > 50) return false
    }
    return true
  })
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

.signal-row {
  cursor: pointer;
  transition: background-color 0.2s;
}

.signal-row:hover {
  background-color: rgba(75, 85, 99, 0.3);
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
