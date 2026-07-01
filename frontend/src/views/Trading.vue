<template>
  <div class="jd-page">
    <!-- Header -->
    <div class="jd-section-header">
      <div>
        <h1 class="jd-section-title">Live Trading</h1>
        <p class="jd-section-description">Execute trades and track your positions</p>
      </div>
    </div>

    <!-- Connection Status -->
    <div class="jd-card">
      <div class="jd-card-body" style="display: flex; align-items: center; justify-content: space-between;">
        <div style="display: flex; align-items: center; gap: 12px;">
          <div class="jd-live-dot" :style="{ background: exchangeConnected ? 'var(--jd-green)' : 'var(--jd-red)' }"></div>
          <span>
            Exchange Status:
            <span :style="{ color: exchangeConnected ? 'var(--jd-green)' : 'var(--jd-red)' }">
              {{ exchangeConnected ? 'Connected' : 'Disconnected' }}
            </span>
            <span v-if="exchangeNames" style="color: var(--jd-text-muted);"> · {{ exchangeNames }}</span>
          </span>
        </div>
        <button class="jd-btn jd-btn-primary" @click="connectExchange">
          <i class="pi pi-link"></i> {{ exchangeConnected ? 'Manage Exchange' : 'Connect Exchange' }}
        </button>
      </div>
    </div>

    <!-- Honesty note: live orders not enabled -->
    <div class="jd-alert info">
      <p style="font-weight: 600; margin-bottom: 4px;">
        <i class="pi pi-info-circle"></i> Live exchange orders aren't enabled yet
      </p>
      <p style="font-size: 0.875rem;">
        Orders placed here are <strong>simulated (paper)</strong> against your virtual portfolio — they do not hit a real
        exchange. Connecting an exchange above tracks its status only; real order routing is not implemented server-side.
      </p>
    </div>

    <!-- Trading Panel -->
    <div style="display: grid; grid-template-columns: 1fr; gap: 20px; max-width: 100%;">
      <div style="display: grid; grid-template-columns: 2fr 1fr; gap: 20px;">
        <!-- Order Entry -->
        <div class="jd-card">
          <div class="jd-card-header">
            <h2 class="jd-card-title">Order Entry <span class="jd-badge" style="margin-left: 8px;">Paper</span></h2>
          </div>
          <div class="jd-card-body">
            <div style="display: flex; flex-direction: column; gap: 16px;">
              <!-- Symbol Selection -->
              <div class="jd-form-group">
                <label class="jd-label">Symbol</label>
                <input v-model="orderSymbol" placeholder="BTCUSDT" class="w-full jd-input" />
              </div>

              <!-- Order Type and Side -->
              <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
                <div class="jd-form-group">
                  <label class="jd-label">Order Type</label>
                  <select v-model="orderType" class="w-full jd-input jd-select">
                    <option v-for="o in orderTypes" :key="o.value" :value="o.value">{{ o.label }}</option>
                  </select>
                </div>
                <div class="jd-form-group">
                  <label class="jd-label">Side</label>
                  <select v-model="orderSide" class="w-full jd-input jd-select">
                    <option v-for="o in orderSides" :key="o.value" :value="o.value">{{ o.label }}</option>
                  </select>
                </div>
              </div>

              <!-- Price and Amount -->
              <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
                <div class="jd-form-group">
                  <label class="jd-label">Price (USDT){{ orderType === 'market' ? ' — optional' : '' }}</label>
                  <input v-model="orderPrice" type="number" min="0" step="any" placeholder="0.00" class="w-full jd-input" />
                </div>
                <div class="jd-form-group">
                  <label class="jd-label">Amount</label>
                  <input v-model="orderQuantity" type="number" min="0" step="any" placeholder="0.00" class="w-full jd-input" />
                </div>
              </div>

              <!-- Total -->
              <div style="background: var(--jd-card); border: 1px solid var(--jd-border); border-radius: 8px; padding: 12px; display: flex; justify-content: space-between; align-items: center;">
                <span style="color: var(--jd-text-muted);">Total (USDT)</span>
                <span style="font-weight: bold; color: var(--jd-text);">{{ formatCurrency(orderTotal) }}</span>
              </div>

              <!-- Form error -->
              <div v-if="formError" class="jd-alert error" style="margin: 0;">
                <p style="font-size: 0.875rem;">{{ formError }}</p>
              </div>

              <!-- Action Buttons -->
              <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px; padding-top: 16px;">
                <button class="jd-btn jd-btn-success jd-btn-lg" :disabled="submitting" @click="placeOrder('buy')">
                  <i class="pi pi-arrow-up"></i> {{ submitting ? 'Placing…' : 'Buy' }}
                </button>
                <button class="jd-btn jd-btn-danger jd-btn-lg" :disabled="submitting" @click="placeOrder('sell')">
                  <i class="pi pi-arrow-down"></i> {{ submitting ? 'Placing…' : 'Sell' }}
                </button>
              </div>
            </div>
          </div>
        </div>

        <!-- Account Info -->
        <div class="jd-card">
          <div class="jd-card-header">
            <h3 class="jd-card-title">Account Info</h3>
          </div>
          <div class="jd-card-body">
            <div style="display: flex; flex-direction: column; gap: 16px;">
              <div>
                <p style="color: var(--jd-text-muted); font-size: 12px; margin-bottom: 4px;">Balance (USDT)</p>
                <p style="font-size: 24px; font-weight: bold; color: var(--jd-text);">{{ formatCurrency(account.current_balance) }}</p>
              </div>
              <div style="border-top: 1px solid var(--jd-border); padding-top: 16px;">
                <p style="color: var(--jd-text-muted); font-size: 12px; margin-bottom: 4px;">Available</p>
                <p style="font-size: 20px; font-weight: bold; color: var(--jd-text);">{{ formatCurrency(account.current_balance) }}</p>
              </div>
              <div style="border-top: 1px solid var(--jd-border); padding-top: 16px;">
                <p style="color: var(--jd-text-muted); font-size: 12px; margin-bottom: 4px;">In Orders</p>
                <p style="font-size: 20px; font-weight: bold; color: var(--jd-text);">{{ formatCurrency(account.total_invested) }}</p>
              </div>
              <div style="border-top: 1px solid var(--jd-border); padding-top: 16px;">
                <p style="color: var(--jd-text-muted); font-size: 12px; margin-bottom: 4px;">Equity</p>
                <p style="font-size: 20px; font-weight: bold;" :class="account.total_pnl >= 0 ? 'price-up' : 'price-down'">
                  {{ formatCurrency(equity) }}
                </p>
                <p style="font-size: 12px; margin-top: 4px;" :class="account.total_pnl >= 0 ? 'price-up' : 'price-down'">
                  P&L {{ formatCurrency(account.total_pnl) }} ({{ formatPercent(account.total_return_percent) }})
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Open Orders -->
      <div class="jd-card">
        <div class="jd-card-header">
          <h2 class="jd-card-title">Open Orders</h2>
        </div>
        <div class="jd-card-body">
          <DataTable
            :columns="openOrderColumns"
            :data="openOrders"
            :row-key="(row) => row.id"
            :searchable="['symbol']"
            search-placeholder="Search open orders…"
            :page-size="10"
            empty-text="No open orders"
          >
            <template #cell:side="{ value }">
              <span class="jd-badge" :class="value === 'Buy' ? 'green' : 'red'">{{ value }}</span>
            </template>
            <template #cell:price="{ value }">{{ formatCurrency(value) }}</template>
            <template #row-actions="{ row }">
              <button class="jd-btn jd-btn-danger jd-btn-sm" :disabled="closingId === row.id" @click="cancelOrder(row.id)">
                <i class="pi pi-times"></i> {{ closingId === row.id ? 'Closing…' : 'Close' }}
              </button>
            </template>
          </DataTable>
        </div>
      </div>

      <!-- Order History -->
      <div class="jd-card">
        <div class="jd-card-header">
          <h2 class="jd-card-title">Order History</h2>
        </div>
        <div class="jd-card-body">
          <DataTable
            :columns="orderHistoryColumns"
            :data="orderHistory"
            :row-key="(row) => row.id"
            :searchable="['symbol']"
            search-placeholder="Search order history…"
            :page-size="10"
            empty-text="No order history"
          >
            <template #cell:side="{ value }">
              <span class="jd-badge" :class="value === 'Buy' ? 'green' : 'red'">{{ value }}</span>
            </template>
            <template #cell:price="{ value }">{{ formatCurrency(value) }}</template>
            <template #cell:timestamp="{ value }">
              <span style="color: var(--jd-text-muted);">{{ formatTime(value) }}</span>
            </template>
          </DataTable>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { getPortfolio, getPortfolioStats, getTrades, placeTrade, closeTrade } from '@/api/trading'
import { exchangeApi } from '@/api/exchange'
import DataTable from '@/components/common/DataTable.vue'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'

dayjs.extend(relativeTime)

const router = useRouter()

// ── Order form state ──────────────────────────────────────────────
const orderSymbol = ref('')
const orderType = ref('limit')
const orderSide = ref('buy')
const orderPrice = ref('')
const orderQuantity = ref('')
const submitting = ref(false)
const formError = ref(null)
const closingId = ref(null)

const orderTypes = ref([
  { label: 'Limit', value: 'limit' },
  { label: 'Market', value: 'market' },
])

const orderSides = ref([
  { label: 'Buy', value: 'buy' },
  { label: 'Sell', value: 'sell' },
])

const orderTotal = computed(() => (Number(orderPrice.value) || 0) * (Number(orderQuantity.value) || 0))

// ── Account / portfolio state ─────────────────────────────────────
const account = ref({
  current_balance: 0, total_invested: 0, current_value: 0,
  total_pnl: 0, total_return_percent: 0,
})
const stats = ref({})
const equity = computed(() => (Number(account.value.current_balance) || 0) + (Number(account.value.current_value) || 0))

// ── Exchange connection state ─────────────────────────────────────
const connections = ref([])
const exchangeConnected = computed(() => connections.value.some((c) => c.is_active !== false))
const exchangeNames = computed(() =>
  connections.value
    .filter((c) => c.is_active !== false)
    .map((c) => String(c.exchange_type || '').toUpperCase())
    .filter(Boolean)
    .join(', ')
)

// ── Tables ────────────────────────────────────────────────────────
const openOrders = ref([])
const orderHistory = ref([])

const openOrderColumns = [
  { key: 'symbol', header: 'Symbol', sortable: true, filterable: true, filterLabel: 'Symbol' },
  { key: 'side', header: 'Side', sortable: true, align: 'center', filterable: true, filterLabel: 'Side' },
  { key: 'price', header: 'Price', sortable: true, align: 'right' },
  { key: 'amount', header: 'Amount', sortable: true, align: 'right' },
  { key: 'status', header: 'Status', sortable: true, align: 'right', filterable: true, filterLabel: 'Status' },
]

const orderHistoryColumns = [
  { key: 'symbol', header: 'Symbol', sortable: true, filterable: true, filterLabel: 'Symbol' },
  { key: 'side', header: 'Side', sortable: true, align: 'center', filterable: true, filterLabel: 'Side' },
  { key: 'price', header: 'Price', sortable: true, align: 'right' },
  { key: 'filledAmount', header: 'Filled', sortable: true, align: 'right' },
  { key: 'status', header: 'Status', sortable: true, align: 'right', filterable: true, filterLabel: 'Status' },
  { key: 'timestamp', header: 'Time', sortable: true, align: 'right' },
]

// ── Formatting (matches Portfolio.vue) ────────────────────────────
const formatCurrency = (value) =>
  new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(Number(value) || 0)
const formatPercent = (value) => `${Number(value || 0).toFixed(2)}%`
const formatTime = (ts) => (ts ? dayjs(ts).fromNow() : '—')

const OPEN_STATUSES = ['open', 'pending']
const toRow = (t) => ({
  id: t.id,
  symbol: t.symbol,
  side: String(t.side).toLowerCase() === 'buy' ? 'Buy' : 'Sell',
  price: t.entry_price ?? t.price ?? 0,
  amount: t.quantity ?? 0,
  filledAmount: t.quantity ?? 0,
  status: t.status,
  timestamp: t.timestamp || t.opened_at || t.created_at,
})

// ── Data loading + polling ────────────────────────────────────────
let refreshInterval = null

const loadData = async () => {
  const [pf, st, tr, ex] = await Promise.allSettled([
    getPortfolio(),
    getPortfolioStats(),
    getTrades({ limit: 50 }),
    exchangeApi.list(),
  ])

  if (pf.status === 'fulfilled') account.value = pf.value.data
  if (st.status === 'fulfilled') stats.value = st.value.data || {}

  if (tr.status === 'fulfilled') {
    const trades = (tr.value.data || []).map(toRow)
    openOrders.value = trades.filter((t) => OPEN_STATUSES.includes(String(t.status).toLowerCase()))
    orderHistory.value = trades
  }

  if (ex.status === 'fulfilled') connections.value = ex.value.data || []
}

const placeOrder = async (side) => {
  formError.value = null
  const symbol = orderSymbol.value.trim().toUpperCase()
  const quantity = Number(orderQuantity.value)
  const price = orderPrice.value === '' || orderPrice.value == null ? null : Number(orderPrice.value)

  if (!symbol) {
    formError.value = 'Enter a symbol (e.g. BTCUSDT).'
    return
  }
  if (!(quantity > 0)) {
    formError.value = 'Amount must be greater than 0.'
    return
  }
  if (orderType.value === 'limit' && !(price > 0)) {
    formError.value = 'Limit orders need a price greater than 0.'
    return
  }

  submitting.value = true
  try {
    const payload = {
      symbol,
      side,
      order_type: orderType.value,
      quantity,
      trading_mode: 'simulate',
    }
    if (price > 0) payload.price = price
    await placeTrade(payload)
    orderSide.value = side
    orderQuantity.value = ''
    await loadData()
  } catch (err) {
    formError.value = err.response?.data?.detail || 'Failed to place order.'
  } finally {
    submitting.value = false
  }
}

const cancelOrder = async (id) => {
  closingId.value = id
  formError.value = null
  try {
    await closeTrade(id)
    await loadData()
  } catch (err) {
    formError.value = err.response?.data?.detail || 'Failed to close order.'
  } finally {
    closingId.value = null
  }
}

const connectExchange = () => router.push('/settings/exchange')

onMounted(() => {
  loadData()
  refreshInterval = setInterval(loadData, 5000)
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
