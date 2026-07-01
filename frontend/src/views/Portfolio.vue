<template>
  <div class="jd-page">
    <!-- Portfolio Summary Stats -->
    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
      <div class="jd-stat-card blue">
        <div class="jd-stat-icon blue"><i class="pi pi-wallet"></i></div>
        <div class="jd-stat-label">Current Value</div>
        <div class="jd-stat-value">{{ formatCurrency(portfolio.current_value) }}</div>
        <div class="jd-stat-sub">Open and closed positions</div>
      </div>

      <div :class="['jd-stat-card', portfolio.total_pnl >= 0 ? 'green' : 'red']">
        <div :class="['jd-stat-icon', portfolio.total_pnl >= 0 ? 'green' : 'red']">
          <i :class="portfolio.total_pnl >= 0 ? 'pi pi-arrow-up' : 'pi pi-arrow-down'"></i>
        </div>
        <div class="jd-stat-label">Total P&L</div>
        <div class="jd-stat-value" :class="portfolio.total_pnl >= 0 ? 'price-up' : 'price-down'">{{ formatCurrency(portfolio.total_pnl) }}</div>
        <div class="jd-stat-sub">Realized and unrealized P&L</div>
      </div>

      <div class="jd-stat-card cyan">
        <div class="jd-stat-icon cyan"><i class="pi pi-chart-line"></i></div>
        <div class="jd-stat-label">Win Rate</div>
        <div class="jd-stat-value">{{ portfolio.win_rate.toFixed(1) }}%</div>
        <div class="jd-stat-sub">{{ portfolio.win_count }} wins / {{ portfolio.loss_count }} losses</div>
      </div>

      <div class="jd-stat-card yellow">
        <div class="jd-stat-icon yellow"><i class="pi pi-inbox"></i></div>
        <div class="jd-stat-label">Open Positions</div>
        <div class="jd-stat-value">{{ portfolio.open_trades || 0 }}</div>
        <div class="jd-stat-sub">Active trades</div>
      </div>
    </div>

    <!-- Positions Table -->
    <DataTable
      :columns="positionColumns"
      :data="portfolio.positions || []"
      :searchable="['symbol']"
      search-placeholder="Search positions…"
      :page-size="10"
      empty-text="No open positions"
    >
      <template #toolbar-right>
        <button @click="refreshPortfolio" :disabled="loading" class="jd-btn jd-btn-primary jd-btn-sm">
          <i :class="['pi', 'pi-refresh', { 'animate-spin': loading }]"></i> Refresh
        </button>
        <button @click="resetPortfolio" :disabled="loading" class="jd-btn jd-btn-danger jd-btn-sm">Reset</button>
      </template>
      <template #cell:entry_price="{ value }">{{ formatCurrency(value) }}</template>
      <template #cell:current_price="{ value }">{{ formatCurrency(value) }}</template>
      <template #cell:pnl="{ value }">
        <span :class="value >= 0 ? 'price-up' : 'price-down'">{{ formatCurrency(value) }}</span>
      </template>
      <template #cell:return_pct="{ value }">
        <span :class="value >= 0 ? 'price-up' : 'price-down'">{{ formatPercent(value) }}</span>
      </template>
      <template #row-actions="{ row }">
        <button @click="closePosition(row.id)" class="jd-btn jd-btn-danger jd-btn-sm">Close</button>
      </template>
    </DataTable>

    <!-- Trade History -->
    <DataTable
      :columns="tradeColumns"
      :data="recentTrades"
      :searchable="['symbol']"
      search-placeholder="Search trades…"
      :page-size="10"
      empty-text="No trade history"
    >
      <template #cell:side="{ value }">
        <span :class="value === 'buy' ? 'price-up' : 'price-down'" class="font-medium">{{ String(value || '').toUpperCase() }}</span>
      </template>
      <template #cell:price="{ value }">{{ formatCurrency(value) }}</template>
      <template #cell:status="{ value }">
        <span :style="{ color: statusColor(value) }">{{ value }}</span>
      </template>
      <template #cell:time="{ value }">
        <span style="color: var(--jd-text-muted)">{{ formatTime(value) }}</span>
      </template>
    </DataTable>

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
import DataTable from '@/components/common/DataTable.vue'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'

dayjs.extend(relativeTime)

const portfolio = ref({
  current_value: 0, current_balance: 0, total_invested: 0, total_pnl: 0,
  unrealized_pnl: 0, realized_pnl: 0, total_return_percent: 0,
  win_rate: 0, win_count: 0, loss_count: 0, open_trades: 0, positions: [],
})

const recentTrades = ref([])
const loading = ref(false)
const error = ref(null)
let refreshInterval = null

const positionColumns = [
  { key: 'symbol', header: 'Symbol', sortable: true },
  { key: 'entry_price', header: 'Entry Price', sortable: true, align: 'right' },
  { key: 'current_price', header: 'Current Price', sortable: true, align: 'right' },
  { key: 'quantity', header: 'Quantity', sortable: true, align: 'right' },
  { key: 'pnl', header: 'P&L', sortable: true, align: 'right' },
  { key: 'return_pct', header: 'Return', sortable: true, align: 'center' },
]

const tradeColumns = [
  { key: 'symbol', header: 'Symbol', sortable: true, filterable: true, filterLabel: 'Symbol' },
  { key: 'side', header: 'Side', sortable: true, align: 'center', filterable: true, filterLabel: 'Side' },
  { key: 'price', header: 'Price', sortable: true, align: 'right' },
  { key: 'quantity', header: 'Quantity', sortable: true, align: 'right' },
  { key: 'status', header: 'Status', sortable: true, align: 'right', filterable: true, filterLabel: 'Status' },
  { key: 'time', header: 'Time', sortable: true, accessor: (r) => r.timestamp || r.opened_at || r.created_at },
]

const formatCurrency = (value) =>
  new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(value || 0)
const formatTime = (timestamp) => (timestamp ? dayjs(timestamp).fromNow() : '—')
const formatPercent = (value) => `${Number(value || 0).toFixed(2)}%`

const STATUS_COLORS = {
  filled: 'var(--jd-green)', pending: 'var(--jd-yellow)',
  cancelled: 'var(--jd-red)', closed: 'var(--jd-text-muted)',
}
const statusColor = (status) => STATUS_COLORS[status] || 'var(--jd-text-muted)'

const refreshPortfolio = async () => {
  loading.value = true
  error.value = null
  try {
    const [portfolioRes, tradesRes] = await Promise.all([getPortfolio(), getTrades({ limit: 50 })])
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
  refreshInterval = setInterval(refreshPortfolio, 5000)
})
onBeforeUnmount(() => {
  if (refreshInterval) clearInterval(refreshInterval)
})
</script>

<style scoped>
.animate-spin { animation: spin 1s linear infinite; }
@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
.price-up { color: var(--jd-green); }
.price-down { color: var(--jd-red); }
</style>
