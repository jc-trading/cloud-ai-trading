<template>
  <div class="jd-page">
    <!-- Header -->
    <div>
      <h1 class="text-3xl font-bold mb-1">Market Overview</h1>
      <p class="text-sm" style="color: var(--jd-text-muted)">Real-time US stock prices</p>
    </div>

    <!-- Stats Row -->
    <div class="stats-grid">
      <div class="jd-stat-card" style="--accent: var(--jd-blue)">
        <div class="jd-stat-label">Tracked</div>
        <div class="jd-stat-value">{{ stockTickers.length }}</div>
      </div>
      <div class="jd-stat-card" style="--accent: var(--jd-blue)">
        <div class="jd-stat-label">NVDA Price</div>
        <div class="jd-stat-value">${{ leadPrice }}</div>
      </div>
      <div class="jd-stat-card" :style="{ '--accent': leadChange >= 0 ? 'var(--jd-green)' : 'var(--jd-red)' }">
        <div class="jd-stat-label">NVDA 24h</div>
        <div class="jd-stat-value" :class="leadChange >= 0 ? 'price-up' : 'price-down'">
          {{ leadChange >= 0 ? '+' : '' }}{{ leadChange }}%
        </div>
      </div>
      <div class="jd-stat-card" style="--accent: var(--jd-blue)">
        <div class="jd-stat-label">Gainers / Losers</div>
        <div class="jd-stat-value">
          <span class="price-up">{{ gainers }}</span>
          <span style="color: var(--jd-text-muted)"> / </span>
          <span class="price-down">{{ losers }}</span>
        </div>
      </div>
    </div>

    <!-- Card with data -->
    <div class="jd-card">
      <div class="jd-card-header flex-header">
        <h2 class="jd-card-title">🇺🇸 US Stocks</h2>

        <div class="header-right">
          <span v-if="loading" class="update-text">Loading...</span>
          <span v-else class="update-text">Updated {{ lastUpdated }}</span>
          <button
            class="jd-btn jd-btn-ghost jd-btn-sm"
            :disabled="loading"
            @click="loadStocks"
          >
            <i class="pi pi-refresh" :class="{ 'animate-spin': loading }"></i>
          </button>
        </div>
      </div>

      <!-- Card Body -->
      <div class="jd-card-body">
        <!-- Error Alert -->
        <div v-if="error" class="jd-alert error mb-4">
          <i class="pi pi-exclamation-circle mr-2"></i>
          {{ error }}
        </div>

        <!-- Alpaca Warning Alert -->
        <div
          v-if="!loading && stockTickers.length === 0 && !error"
          class="jd-alert warning mb-4"
        >
          <p class="font-semibold mb-1">Alpaca API Keys Required</p>
          <p class="text-sm mb-2">
            US stock data requires Alpaca API keys. Add <code>ALPACA_API_KEY</code>
            and <code>ALPACA_API_SECRET</code> to your <code>.env</code> file.
          </p>
          <p class="text-xs">Sign up free at <a href="https://alpaca.markets" target="_blank">alpaca.markets</a></p>
        </div>

        <!-- Data Table (reusable component) -->
        <DataTable
          :columns="columns"
          :data="stockTickers"
          row-key="symbol"
          :searchable="['symbol']"
          search-placeholder="Search symbol…"
          :page-size="25"
          :loading="loading"
          clickable-rows
          @row-click="goToSymbol"
        >
          <template #cell:symbol="{ value }">
            <div class="flex items-center gap-2">
              <span class="font-semibold">{{ value }}</span>
              <span class="jd-badge blue">US</span>
            </div>
          </template>
          <template #cell:last="{ value }">
            <span class="font-mono">${{ formatPrice(value) }}</span>
          </template>
          <template #cell:change_24h="{ value }">
            <span
              class="font-semibold"
              :class="(value ?? 0) >= 0 ? 'price-up' : 'price-down'"
            >
              {{ (value ?? 0) >= 0 ? '+' : '' }}{{ (value ?? 0).toFixed(2) }}%
            </span>
          </template>
          <template #cell:high="{ value }">
            <span class="font-mono" style="color: var(--jd-text-muted)">${{ formatPrice(value) }}</span>
          </template>
          <template #cell:low="{ value }">
            <span class="font-mono" style="color: var(--jd-text-muted)">${{ formatPrice(value) }}</span>
          </template>
          <template #cell:volume="{ value }">
            <span style="color: var(--jd-text-muted)">{{ formatVolume(value) }}</span>
          </template>

          <template #empty>
            <div class="jd-empty">
              <i class="pi pi-chart-bar"></i>
              <p>No stock data — Alpaca keys needed</p>
            </div>
          </template>
        </DataTable>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import DataTable from '@/components/common/DataTable.vue'
import { marketApi } from '@/api/market'

const router = useRouter()

// ── State ───────────────────────────────────────────────────────
const stockTickers = ref([])
const loading = ref(false)
const error = ref(null)
const lastUpdated = ref('--')

// ── Table columns ───────────────────────────────────────────────
const columns = [
  { key: 'symbol', header: 'Symbol', sortable: true },
  { key: 'last', header: 'Price', sortable: true, align: 'right' },
  { key: 'change_24h', header: '24h Change', sortable: true, align: 'right' },
  { key: 'high', header: 'Day High', sortable: true, align: 'right' },
  { key: 'low', header: 'Day Low', sortable: true, align: 'right' },
  { key: 'volume', header: 'Volume', sortable: true, align: 'right' },
]

// ── Computed ────────────────────────────────────────────────────
const leadTicker = computed(() =>
  stockTickers.value.find(t => t.symbol === 'NVDA')
)

const leadPrice = computed(() =>
  leadTicker.value ? formatPrice(leadTicker.value.last) : '--'
)
const leadChange = computed(() =>
  leadTicker.value ? (leadTicker.value.change_24h ?? 0).toFixed(2) : '--'
)
const gainers = computed(() =>
  stockTickers.value.filter(t => (t.change_24h ?? 0) >= 0).length
)
const losers = computed(() =>
  stockTickers.value.filter(t => (t.change_24h ?? 0) < 0).length
)

// ── Formatters ──────────────────────────────────────────────────
function formatPrice(val) {
  if (!val && val !== 0) return '--'
  if (val >= 1000) return val.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  if (val >= 1) return val.toFixed(4)
  return val.toFixed(6)
}

function formatVolume(val) {
  if (!val) return '--'
  if (val >= 1_000_000_000) return (val / 1_000_000_000).toFixed(2) + 'B'
  if (val >= 1_000_000) return (val / 1_000_000).toFixed(2) + 'M'
  if (val >= 1_000) return (val / 1_000).toFixed(2) + 'K'
  return val.toFixed(0)
}

// ── Row navigation → SymbolDetail ───────────────────────────────
function goToSymbol(row) {
  router.push(`/market/${encodeURIComponent(row.symbol)}`)
}

// ── Loader ──────────────────────────────────────────────────────
async function loadStocks() {
  loading.value = true
  error.value = null
  try {
    const res = await marketApi.getStockTickers()
    stockTickers.value = res.data
    lastUpdated.value = new Date().toLocaleTimeString()
  } catch (err) {
    error.value = err.response?.data?.detail || err.message || 'Failed to load stock data'
    console.error('Stock load error:', err)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadStocks()
})
</script>

<style scoped>
.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 20px;
}

.flex-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.update-text {
  font-size: 0.875rem;
  color: var(--jd-text-muted);
}

.mb-4 {
  margin-bottom: 16px;
}
</style>
