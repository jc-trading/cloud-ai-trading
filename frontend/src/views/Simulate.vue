<template>
  <div class="jd-page">
    <!-- Header -->
    <div class="jd-section-header">
      <div>
        <h1 class="jd-section-title">Paper Trading Simulator</h1>
        <p class="jd-section-description">Practice trading with virtual capital in a risk-free environment</p>
      </div>
      <button @click="onReset" :disabled="loading" class="jd-btn jd-btn-danger jd-btn-sm">
        <i class="pi pi-refresh"></i> Reset Portfolio
      </button>
    </div>

    <!-- Simulator Controls (Backtesting — not yet supported by backend) -->
    <div class="jd-card">
      <div class="jd-card-body" style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; align-items: flex-end;">
        <div class="jd-form-group">
          <label class="jd-label">Simulation Mode</label>
          <select v-model="simulationMode" disabled class="jd-input jd-select w-full">
            <option v-for="o in modes" :key="o.value" :value="o.value">{{ o.label }}</option>
          </select>
        </div>
        <div class="jd-form-group">
          <label class="jd-label">Start Date</label>
          <input type="date" v-model="startDate" disabled class="jd-input w-full" />
        </div>
        <div class="jd-form-group">
          <label class="jd-label">End Date</label>
          <input type="date" v-model="endDate" disabled class="jd-input w-full" />
        </div>
        <div>
          <button class="jd-btn jd-btn-primary" disabled title="Backtesting not available yet">
            <i class="pi pi-play"></i> Start Simulation
          </button>
        </div>
      </div>
      <div class="jd-card-body" style="padding-top: 0;">
        <p style="font-size: 12px; color: var(--jd-text-muted); margin: 0;">
          <i class="pi pi-info-circle"></i>
          Date-range backtesting is not available yet. Use the live paper order entry below to place virtual trades.
        </p>
      </div>
    </div>

    <!-- Simulator Stats -->
    <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px;">
      <div class="jd-stat-card blue">
        <div class="jd-stat-icon blue"><i class="pi pi-wallet"></i></div>
        <p class="jd-stat-label">Virtual Balance</p>
        <p class="jd-stat-value">{{ formatCurrency(portfolio.current_value) }}</p>
      </div>
      <div class="jd-stat-card green">
        <div class="jd-stat-icon green"><i class="pi pi-dollar"></i></div>
        <p class="jd-stat-label">Start Balance</p>
        <p class="jd-stat-value">{{ formatCurrency(startBalance) }}</p>
      </div>
      <div :class="['jd-stat-card', portfolio.total_pnl >= 0 ? 'cyan' : 'red']">
        <div :class="['jd-stat-icon', portfolio.total_pnl >= 0 ? 'cyan' : 'red']">
          <i :class="portfolio.total_pnl >= 0 ? 'pi pi-arrow-up' : 'pi pi-arrow-down'"></i>
        </div>
        <p class="jd-stat-label">P/L</p>
        <p class="jd-stat-value" :class="portfolio.total_pnl >= 0 ? 'price-up' : 'price-down'">{{ formatCurrency(portfolio.total_pnl) }}</p>
      </div>
      <div :class="['jd-stat-card', portfolio.total_return_percent >= 0 ? 'purple' : 'red']">
        <div :class="['jd-stat-icon', portfolio.total_return_percent >= 0 ? 'purple' : 'red']"><i class="pi pi-chart-line"></i></div>
        <p class="jd-stat-label">Return %</p>
        <p class="jd-stat-value" :class="portfolio.total_return_percent >= 0 ? 'price-up' : 'price-down'">{{ formatPercent(portfolio.total_return_percent) }}</p>
      </div>
    </div>

    <!-- Simulator Trading Panel -->
    <div style="display: grid; grid-template-columns: 2fr 1fr; gap: 20px;">
      <!-- Order Entry -->
      <div class="jd-card">
        <div class="jd-card-header">
          <h2 class="jd-card-title">Simulator Order Entry</h2>
        </div>
        <div class="jd-card-body">
          <form @submit.prevent style="display: flex; flex-direction: column; gap: 16px;">
            <!-- Symbol Selection -->
            <div class="jd-form-group">
              <label class="jd-label">Symbol</label>
              <input v-model="orderSymbol" placeholder="BTCUSDT" class="jd-input w-full" />
            </div>

            <!-- Order Type and Side -->
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
              <div class="jd-form-group">
                <label class="jd-label">Order Type</label>
                <select v-model="orderType" class="jd-input jd-select w-full">
                  <option v-for="o in orderTypes" :key="o.value" :value="o.value">{{ o.label }}</option>
                </select>
              </div>
              <div class="jd-form-group">
                <label class="jd-label">Side</label>
                <select v-model="orderSide" class="jd-input jd-select w-full">
                  <option v-for="o in orderSides" :key="o.value" :value="o.value">{{ o.label }}</option>
                </select>
              </div>
            </div>

            <!-- Price and Amount -->
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
              <div class="jd-form-group">
                <label class="jd-label">Price (USDT)</label>
                <input v-model="orderPrice" type="number" step="any" placeholder="0.00" :disabled="orderType === 'market'" class="jd-input w-full" />
              </div>
              <div class="jd-form-group">
                <label class="jd-label">Amount</label>
                <input v-model="orderAmount" type="number" step="any" placeholder="0.00" class="jd-input w-full" />
              </div>
            </div>

            <!-- Total -->
            <div style="background: var(--jd-card); border: 1px solid var(--jd-border); border-radius: 8px; padding: 12px; display: flex; justify-content: space-between; align-items: center;">
              <span style="color: var(--jd-text-muted);">Total (USDT)</span>
              <span style="font-weight: bold; color: var(--jd-text);">{{ orderTotal }}</span>
            </div>

            <!-- Error -->
            <div v-if="error" class="jd-alert error" style="margin: 0;">
              <p style="font-size: 0.875rem; margin: 0;">{{ error }}</p>
            </div>

            <!-- Action Buttons -->
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px; padding-top: 16px;">
              <button type="button" @click="submitOrder('buy')" :disabled="submitting" class="jd-btn jd-btn-success jd-btn-lg">
                <i class="pi pi-arrow-up"></i> Buy
              </button>
              <button type="button" @click="submitOrder('sell')" :disabled="submitting" class="jd-btn jd-btn-danger jd-btn-lg">
                <i class="pi pi-arrow-down"></i> Sell
              </button>
            </div>
          </form>
        </div>
      </div>

      <!-- Portfolio Info -->
      <div class="jd-card">
        <div class="jd-card-header">
          <h3 class="jd-card-title">Portfolio</h3>
        </div>
        <div class="jd-card-body">
          <div style="display: flex; flex-direction: column; gap: 16px;">
            <div>
              <p style="color: var(--jd-text-muted); font-size: 12px; margin-bottom: 4px;">Cash</p>
              <p style="font-size: 24px; font-weight: bold; color: var(--jd-text);">{{ formatCurrency(portfolio.current_balance) }}</p>
            </div>
            <div style="border-top: 1px solid var(--jd-border); padding-top: 16px;">
              <p style="color: var(--jd-text-muted); font-size: 12px; margin-bottom: 4px;">Positions</p>
              <p style="font-size: 20px; font-weight: bold; color: var(--jd-text);">{{ portfolio.open_trades || 0 }}</p>
            </div>
            <div style="border-top: 1px solid var(--jd-border); padding-top: 16px;">
              <p style="color: var(--jd-text-muted); font-size: 12px; margin-bottom: 4px;">Total Trades</p>
              <p style="font-size: 20px; font-weight: bold; color: var(--jd-text);">{{ tradeHistory.length }}</p>
            </div>
            <div style="border-top: 1px solid var(--jd-border); padding-top: 16px;">
              <p style="color: var(--jd-text-muted); font-size: 12px; margin-bottom: 4px;">Win Rate</p>
              <p style="font-size: 20px; font-weight: bold; color: var(--jd-text);">{{ formatPercent(portfolio.win_rate) }}</p>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Open Positions -->
    <DataTable
      :columns="positionColumns"
      :data="openPositions"
      :row-key="(row) => row.id ?? row.symbol"
      :searchable="['symbol']"
      search-placeholder="Search positions…"
      empty-text="No open positions"
    >
      <template #toolbar-left>
        <h2 class="jd-card-title">Open Positions</h2>
      </template>
      <template #cell:entry_price="{ value }">{{ formatCurrency(value) }}</template>
      <template #cell:current_price="{ value }">{{ formatCurrency(value) }}</template>
      <template #cell:pnl="{ value }">
        <span :class="value >= 0 ? 'price-up' : 'price-down'">{{ formatCurrency(value) }}</span>
      </template>
      <template #cell:return_pct="{ value }">
        <span :class="value >= 0 ? 'price-up' : 'price-down'">{{ value >= 0 ? '+' : '' }}{{ formatPercent(value) }}</span>
      </template>
      <template #row-actions="{ row }">
        <button @click="onClosePosition(row.id)" :disabled="submitting" class="jd-btn jd-btn-ghost jd-btn-sm">
          <i class="pi pi-times"></i> Close
        </button>
      </template>
    </DataTable>

    <!-- Trade History -->
    <DataTable
      :columns="tradeColumns"
      :data="tradeHistory"
      :row-key="(row) => row.id ?? `${row.timestamp}-${row.symbol}`"
      :searchable="['symbol']"
      search-placeholder="Search trades…"
      empty-text="No trades executed yet"
    >
      <template #toolbar-left>
        <h2 class="jd-card-title">Trade History</h2>
      </template>
      <template #cell:side="{ value }">
        <span class="jd-badge" :class="String(value).toLowerCase() === 'buy' ? 'green' : 'red'">{{ String(value || '').toUpperCase() }}</span>
      </template>
      <template #cell:price="{ value }">{{ formatCurrency(value) }}</template>
      <template #cell:time="{ value }">
        <span style="color: var(--jd-text-muted)">{{ formatTime(value) }}</span>
      </template>
    </DataTable>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { getPortfolio, getTrades, placeTrade, closeTrade, resetPortfolio as resetPortfolioAPI } from '@/api/trading'
import DataTable from '@/components/common/DataTable.vue'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'

dayjs.extend(relativeTime)

const START_BALANCE = 10000

// ── Backtest controls (disabled — no backend endpoint) ──────────
const simulationMode = ref('historical')
const startDate = ref(null)
const endDate = ref(null)

const modes = ref([
  { label: 'Historical Data', value: 'historical' },
  { label: 'Live Paper Trading', value: 'live' },
  { label: 'Custom Dataset', value: 'custom' }
])

// ── Order entry form ────────────────────────────────────────────
const orderSymbol = ref('')
const orderType = ref('market')
const orderSide = ref('buy')
const orderPrice = ref('')
const orderAmount = ref('')

const orderTypes = ref([
  { label: 'Market', value: 'market' },
  { label: 'Limit', value: 'limit' }
])

const orderSides = ref([
  { label: 'Buy', value: 'buy' },
  { label: 'Sell', value: 'sell' }
])

const orderTotal = computed(() => {
  const price = Number(orderPrice.value)
  const amount = Number(orderAmount.value)
  if (!price || !amount) return '0.00'
  return (price * amount).toFixed(2)
})

// ── Portfolio / trades state ────────────────────────────────────
const portfolio = ref({
  current_value: 0, current_balance: 0, total_pnl: 0, total_return_percent: 0,
  win_rate: 0, open_trades: 0, positions: [],
})
const tradeHistory = ref([])
const openPositions = computed(() => portfolio.value.positions || [])
const startBalance = computed(() => portfolio.value.current_balance || START_BALANCE)

const loading = ref(false)
const submitting = ref(false)
const error = ref(null)
let refreshInterval = null

const positionColumns = [
  { key: 'symbol', header: 'Symbol', sortable: true },
  { key: 'quantity', header: 'Amount', sortable: true, align: 'right' },
  { key: 'entry_price', header: 'Entry Price', sortable: true, align: 'right' },
  { key: 'current_price', header: 'Current Price', sortable: true, align: 'right' },
  { key: 'pnl', header: 'P/L', sortable: true, align: 'right' },
  { key: 'return_pct', header: 'Return', sortable: true, align: 'center' },
]

const tradeColumns = [
  { key: 'symbol', header: 'Symbol', sortable: true, filterable: true, filterLabel: 'Symbol' },
  { key: 'side', header: 'Side', sortable: true, align: 'center', filterable: true, filterLabel: 'Side' },
  { key: 'price', header: 'Price', sortable: true, align: 'right' },
  { key: 'quantity', header: 'Amount', sortable: true, align: 'right' },
  { key: 'status', header: 'Status', sortable: true, align: 'right', filterable: true, filterLabel: 'Status' },
  { key: 'time', header: 'Time', sortable: true, accessor: (r) => r.timestamp || r.opened_at || r.created_at },
]

// ── Formatting (mirrors Portfolio.vue) ──────────────────────────
const formatCurrency = (value) =>
  new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(value || 0)
const formatPercent = (value) => `${Number(value || 0).toFixed(2)}%`
const formatTime = (timestamp) => (timestamp ? dayjs(timestamp).fromNow() : '—')

// ── Data loading ────────────────────────────────────────────────
const refresh = async () => {
  loading.value = true
  try {
    const [portfolioRes, tradesRes] = await Promise.all([getPortfolio(), getTrades({ limit: 50 })])
    portfolio.value = portfolioRes.data
    tradeHistory.value = tradesRes.data || []
  } catch (err) {
    error.value = err.response?.data?.detail || 'Failed to load portfolio'
    console.error('Error:', err)
  } finally {
    loading.value = false
  }
}

// ── Actions ─────────────────────────────────────────────────────
const submitOrder = async (side) => {
  error.value = null
  orderSide.value = side

  const symbol = orderSymbol.value.trim().toUpperCase()
  const quantity = Number(orderAmount.value)
  const price = Number(orderPrice.value)

  if (!symbol) { error.value = 'Symbol is required'; return }
  if (!quantity || quantity <= 0) { error.value = 'Amount must be greater than 0'; return }
  if (orderType.value === 'limit' && (!price || price <= 0)) { error.value = 'Limit orders require a valid price'; return }

  const payload = {
    symbol,
    side,
    order_type: orderType.value,
    quantity,
    price: orderType.value === 'limit' ? price : undefined,
    trading_mode: 'simulate',
  }

  submitting.value = true
  try {
    await placeTrade(payload)
    orderAmount.value = ''
    orderPrice.value = ''
    await refresh()
  } catch (err) {
    error.value = err.response?.data?.detail || 'Failed to place order'
  } finally {
    submitting.value = false
  }
}

const onClosePosition = async (positionId) => {
  if (positionId == null) return
  if (!confirm('Close this position?')) return
  error.value = null
  submitting.value = true
  try {
    await closeTrade(positionId)
    await refresh()
  } catch (err) {
    error.value = err.response?.data?.detail || 'Failed to close position'
  } finally {
    submitting.value = false
  }
}

const onReset = async () => {
  if (!confirm('Reset the simulated portfolio to its starting balance? This cannot be undone.')) return
  error.value = null
  submitting.value = true
  try {
    const res = await resetPortfolioAPI()
    portfolio.value = res.data
    tradeHistory.value = []
    await refresh()
  } catch (err) {
    error.value = err.response?.data?.detail || 'Failed to reset portfolio'
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  refresh()
  refreshInterval = setInterval(refresh, 5000)
})
onBeforeUnmount(() => {
  if (refreshInterval) clearInterval(refreshInterval)
})
</script>

<style scoped>
.w-full {
  width: 100%;
}

.price-up {
  color: var(--jd-green);
}

.price-down {
  color: var(--jd-red);
}
</style>
