<template>
  <div class="jd-page">
    <!-- Portfolio Summary Stats -->
    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
      <div class="jd-stat-card blue">
        <div class="jd-stat-icon blue">
          <i class="pi pi-wallet"></i>
        </div>
        <div class="jd-stat-label">Current Value</div>
        <div class="jd-stat-value">{{ formatCurrency(portfolio.current_value) }}</div>
        <div class="jd-stat-sub">Open and closed positions</div>
      </div>

      <div :class="['jd-stat-card', portfolio.total_pnl >= 0 ? 'green' : 'red']">
        <div :class="['jd-stat-icon', portfolio.total_pnl >= 0 ? 'green' : 'red']">
          <i :class="portfolio.total_pnl >= 0 ? 'pi pi-arrow-up' : 'pi pi-arrow-down'"></i>
        </div>
        <div class="jd-stat-label">Total P&L</div>
        <div class="jd-stat-value">{{ formatCurrency(portfolio.total_pnl) }}</div>
        <div class="jd-stat-sub">Realized and unrealized P&L</div>
      </div>

      <div class="jd-stat-card blue">
        <div class="jd-stat-icon blue">
          <i class="pi pi-chart-line"></i>
        </div>
        <div class="jd-stat-label">Win Rate</div>
        <div class="jd-stat-value">{{ portfolio.win_rate.toFixed(1) }}%</div>
        <div class="jd-stat-sub">{{ portfolio.win_count }} wins / {{ portfolio.loss_count }} losses</div>
      </div>

      <div class="jd-stat-card yellow">
        <div class="jd-stat-icon yellow">
          <i class="pi pi-inbox"></i>
        </div>
        <div class="jd-stat-label">Open Positions</div>
        <div class="jd-stat-value">{{ portfolio.open_trades || 0 }}</div>
        <div class="jd-stat-sub">Active trades</div>
      </div>
    </div>

    <!-- Toolbar -->
    <div class="flex gap-2 justify-end">
      <button
        @click="refreshPortfolio"
        :disabled="loading"
        class="jd-btn jd-btn-primary jd-btn-sm flex items-center gap-2"
      >
        <i :class="['pi', 'pi-refresh', { 'animate-spin': loading }]"></i>
        Refresh
      </button>
      <button
        @click="resetPortfolio"
        :disabled="loading"
        class="jd-btn jd-btn-danger jd-btn-sm"
      >
        Reset
      </button>
    </div>

    <!-- Positions Table -->
    <div class="jd-card">
      <div class="jd-card-header">
        <h2 class="jd-card-title">Open Positions</h2>
        <span class="text-sm" style="color: var(--jd-text-muted)">{{ portfolio.open_trades || 0 }} positions</span>
      </div>
      <div class="overflow-x-auto">
        <table class="jd-table">
          <thead>
            <tr>
              <th>Symbol</th>
              <th style="text-align: right">Entry Price</th>
              <th style="text-align: right">Current Price</th>
              <th style="text-align: right">Quantity</th>
              <th style="text-align: right">P&L</th>
              <th style="text-align: center">Return</th>
              <th style="text-align: center">Action</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="portfolio.positions && portfolio.positions.length > 0" v-for="position in portfolio.positions" :key="position.id">
              <td>{{ position.symbol }}</td>
              <td style="text-align: right">{{ formatCurrency(position.entry_price) }}</td>
              <td style="text-align: right">{{ formatCurrency(position.current_price) }}</td>
              <td style="text-align: right">{{ position.quantity }}</td>
              <td :class="position.pnl >= 0 ? 'price-up' : 'price-down'" style="text-align: right">
                {{ formatCurrency(position.pnl) }}
              </td>
              <td :class="position.return_pct >= 0 ? 'price-up' : 'price-down'" style="text-align: center">
                {{ formatPercent(position.return_pct) }}
              </td>
              <td style="text-align: center">
                <button
                  @click="closePosition(position.id)"
                  class="jd-btn jd-btn-danger jd-btn-sm"
                >
                  Close
                </button>
              </td>
            </tr>
            <tr v-else>
              <td colspan="7" style="text-align: center; padding: 32px 16px">
                <span style="color: var(--jd-text-muted)">No open positions</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Trade History -->
    <div class="jd-card">
      <div class="jd-card-header">
        <h2 class="jd-card-title">Recent Trades</h2>
      </div>
      <div class="overflow-x-auto">
        <table class="jd-table">
          <thead>
            <tr>
              <th>Symbol</th>
              <th style="text-align: center">Side</th>
              <th style="text-align: right">Price</th>
              <th style="text-align: right">Quantity</th>
              <th style="text-align: right">Status</th>
              <th>Time</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="recentTrades.length > 0" v-for="trade in recentTrades.slice(0, 10)" :key="trade.id">
              <td>{{ trade.symbol }}</td>
              <td style="text-align: center">
                <span :class="trade.side === 'buy' ? 'price-up' : 'price-down'" class="font-medium">
                  {{ trade.side.toUpperCase() }}
                </span>
              </td>
              <td style="text-align: right">{{ formatCurrency(trade.price) }}</td>
              <td style="text-align: right">{{ trade.quantity }}</td>
              <td :class="statusColor(trade.status)" style="text-align: right">
                {{ trade.status }}
              </td>
              <td style="color: var(--jd-text-muted); font-size: 0.875rem">{{ formatTime(trade.timestamp || trade.opened_at || trade.created_at) }}</td>
            </tr>
            <tr v-else>
              <td colspan="6" style="text-align: center; padding: 32px 16px">
                <span style="color: var(--jd-text-muted)">No trade history</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Error State -->
    <div v-if="error" class="jd-alert error">
      <p style="font-weight: 600; margin-bottom: 8px">Error</p>
      <p style="font-size: 0.875rem">{{ error }}</p>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { getPortfolio, getTrades, resetPortfolio as resetPortfolioAPI, closeTrade } from '@/api/trading'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'

dayjs.extend(relativeTime)

const portfolio = ref({
  current_value: 0,
  current_balance: 0,
  total_invested: 0,
  total_pnl: 0,
  unrealized_pnl: 0,
  realized_pnl: 0,
  total_return_percent: 0,
  win_rate: 0,
  win_count: 0,
  loss_count: 0,
  open_trades: 0,
  positions: [],
})

const recentTrades = ref([])
const loading = ref(false)
const error = ref(null)
let refreshInterval = null

const formatCurrency = (value) => {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
  }).format(value || 0)
}

const formatTime = (timestamp) => {
  return dayjs(timestamp).fromNow()
}

const formatPercent = (value) => {
  return `${Number(value || 0).toFixed(2)}%`
}

const statusColor = (status) => {
  const colorMap = {
    'filled': 'text-green-400',
    'pending': 'text-yellow-400',
    'cancelled': 'text-red-400',
    'closed': 'text-gray-400',
  }
  return colorMap[status] || 'text-gray-400'
}

const refreshPortfolio = async () => {
  loading.value = true
  error.value = null
  try {
    const [portfolioRes, tradesRes] = await Promise.all([
      getPortfolio(),
      getTrades({ limit: 50 }),
    ])
    portfolio.value = portfolioRes.data
    recentTrades.value = tradesRes.data || []
  } catch (err) {
    error.value = err.response?.data?.detail || 'Failed to load portfolio'
    console.error('Error:', err)
  } finally {
    loading.value = false
  }
}

const resetPortfolio = async () => {
  if (!confirm('Reset portfolio to initial balance? This cannot be undone.')) return

  loading.value = true
  try {
    const res = await resetPortfolioAPI()
    portfolio.value = res.data
    recentTrades.value = []
  } catch (err) {
    error.value = err.response?.data?.detail || 'Failed to reset portfolio'
  } finally {
    loading.value = false
  }
}

const closePosition = async (positionId) => {
  if (!confirm('Close this position?')) return

  loading.value = true
  error.value = null
  try {
    await closeTrade(positionId)
    await refreshPortfolio()
  } catch (err) {
    error.value = err.response?.data?.detail || 'Failed to close position'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  refreshPortfolio()
  // Auto-refresh every 5 seconds
  refreshInterval = setInterval(refreshPortfolio, 5000)
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
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.price-up {
  color: var(--jd-green);
}

.price-down {
  color: var(--jd-red);
}
</style>
