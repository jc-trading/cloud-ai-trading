<template>
  <div class="jd-page">
    <!-- Header -->
    <div>
      <h1 class="text-3xl font-bold mb-1">Market Overview</h1>
      <p class="text-sm" style="color: var(--jd-text-muted)">Real-time crypto and US stock prices</p>
    </div>

    <!-- Stats Row -->
    <div class="stats-grid">
      <div class="jd-stat-card" style="--accent: var(--jd-blue)">
        <div class="jd-stat-label">Tracked</div>
        <div class="jd-stat-value">{{ activeTickers.length }}</div>
      </div>
      <div class="jd-stat-card" style="--accent: var(--jd-blue)">
        <div class="jd-stat-label">{{ activeTab === 'crypto' ? 'BTC Price' : 'NVDA Price' }}</div>
        <div class="jd-stat-value">${{ leadPrice }}</div>
      </div>
      <div class="jd-stat-card" :style="{ '--accent': leadChange >= 0 ? 'var(--jd-green)' : 'var(--jd-red)' }">
        <div class="jd-stat-label">{{ activeTab === 'crypto' ? 'BTC 24h' : 'NVDA 24h' }}</div>
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

    <!-- Card with tabs and data -->
    <div class="jd-card">
      <!-- Card Header -->
      <div class="jd-card-header flex-header">
        <div class="jd-tabs">
          <button
            v-for="tab in tabs"
            :key="tab.key"
            @click="switchTab(tab.key)"
            class="jd-tab"
            :class="{ active: activeTab === tab.key }"
          >
            {{ tab.label }}
          </button>
        </div>

        <div class="header-right">
          <span v-if="loading" class="update-text">Loading...</span>
          <span v-else class="update-text">Updated {{ lastUpdated }}</span>
          <Button
            icon="pi pi-refresh"
            class="p-button-sm p-button-text p-button-rounded"
            :loading="loading"
            @click="loadActiveTab"
          />
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
          v-if="activeTab === 'stocks' && !loading && activeTickers.length === 0 && !error"
          class="jd-alert warning mb-4"
        >
          <p class="font-semibold mb-1">Alpaca API Keys Required</p>
          <p class="text-sm mb-2">
            US stock data requires Alpaca API keys. Add <code>ALPACA_API_KEY</code>
            and <code>ALPACA_API_SECRET</code> to your <code>.env</code> file.
          </p>
          <p class="text-xs">Sign up free at <a href="https://alpaca.markets" target="_blank">alpaca.markets</a></p>
        </div>

        <!-- Data Table -->
        <DataTable
          :value="activeTickers"
          :loading="loading"
          stripedRows
          responsiveLayout="scroll"
          class="p-datatable-sm"
          :paginator="activeTickers.length > 10"
          :rows="10"
        >
          <Column field="symbol" header="Symbol" :sortable="true">
            <template #body="{ data }">
              <div class="flex items-center gap-2">
                <span class="font-semibold">{{ data.symbol }}</span>
                <span v-if="activeTab === 'stocks'" class="jd-badge blue">US</span>
              </div>
            </template>
          </Column>
          <Column field="last" header="Price" :sortable="true">
            <template #body="{ data }">
              <span class="font-mono">${{ formatPrice(data.last) }}</span>
            </template>
          </Column>
          <Column field="change_24h" header="24h Change" :sortable="true">
            <template #body="{ data }">
              <span
                class="font-semibold"
                :class="(data.change_24h ?? 0) >= 0 ? 'price-up' : 'price-down'"
              >
                {{ (data.change_24h ?? 0) >= 0 ? '+' : '' }}{{ (data.change_24h ?? 0).toFixed(2) }}%
              </span>
            </template>
          </Column>
          <Column field="high" header="Day High" :sortable="true">
            <template #body="{ data }">
              <span class="font-mono" style="color: var(--jd-text-muted)">${{ formatPrice(data.high) }}</span>
            </template>
          </Column>
          <Column field="low" header="Day Low" :sortable="true">
            <template #body="{ data }">
              <span class="font-mono" style="color: var(--jd-text-muted)">${{ formatPrice(data.low) }}</span>
            </template>
          </Column>
          <Column field="volume" header="Volume" :sortable="true">
            <template #body="{ data }">
              <span style="color: var(--jd-text-muted)">{{ formatVolume(data.volume) }}</span>
            </template>
          </Column>
          <Column header="Action">
            <template #body="{ data }">
              <router-link :to="`/market/${encodeURIComponent(data.symbol)}`">
                <Button label="View" class="p-button-sm p-button-rounded p-button-outlined" />
              </router-link>
            </template>
          </Column>

          <template #empty>
            <div class="jd-empty">
              <i class="pi pi-chart-bar"></i>
              <p>{{ activeTab === 'stocks' ? 'No stock data — Alpaca keys needed' : 'No market data available' }}</p>
            </div>
          </template>
        </DataTable>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Button from 'primevue/button'
import { marketApi } from '@/api/market'

// ── State ───────────────────────────────────────────────────────
const activeTab = ref('crypto')
const cryptoTickers = ref([])
const stockTickers = ref([])
const loading = ref(false)
const error = ref(null)
const lastUpdated = ref('--')

const tabs = [
  { key: 'crypto', label: '🔶 Crypto' },
  { key: 'stocks', label: '🇺🇸 US Stocks' },
]

// ── Computed ────────────────────────────────────────────────────
const activeTickers = computed(() =>
  activeTab.value === 'crypto' ? cryptoTickers.value : stockTickers.value
)

const leadTicker = computed(() => {
  if (activeTab.value === 'crypto') {
    return activeTickers.value.find(t => t.symbol === 'BTC/USDT')
  }
  return activeTickers.value.find(t => t.symbol === 'NVDA')
})

const leadPrice = computed(() =>
  leadTicker.value ? formatPrice(leadTicker.value.last) : '--'
)
const leadChange = computed(() =>
  leadTicker.value ? (leadTicker.value.change_24h ?? 0).toFixed(2) : '--'
)
const gainers = computed(() =>
  activeTickers.value.filter(t => (t.change_24h ?? 0) >= 0).length
)
const losers = computed(() =>
  activeTickers.value.filter(t => (t.change_24h ?? 0) < 0).length
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

// ── Tab switching ───────────────────────────────────────────────
async function switchTab(key) {
  activeTab.value = key
  // Load on demand if not yet loaded
  if (key === 'crypto' && cryptoTickers.value.length === 0) {
    await loadCrypto()
  } else if (key === 'stocks' && stockTickers.value.length === 0) {
    await loadStocks()
  }
}

// ── Loaders ─────────────────────────────────────────────────────
async function loadCrypto() {
  loading.value = true
  error.value = null
  try {
    const res = await marketApi.getTickers()
    cryptoTickers.value = res.data
    lastUpdated.value = new Date().toLocaleTimeString()
  } catch (err) {
    error.value = err.response?.data?.detail || err.message || 'Failed to load crypto data'
    console.error('Crypto load error:', err)
  } finally {
    loading.value = false
  }
}

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

async function loadActiveTab() {
  if (activeTab.value === 'crypto') {
    await loadCrypto()
  } else {
    await loadStocks()
  }
}

onMounted(() => {
  loadCrypto()
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

:deep(.p-datatable) {
  background-color: transparent;
  color: var(--jd-text);
}

:deep(.p-datatable .p-datatable-thead > tr > th) {
  background-color: rgba(75, 85, 99, 0.4);
  color: var(--jd-text-muted);
  border-color: var(--jd-border);
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

:deep(.p-datatable .p-datatable-tbody > tr) {
  background-color: transparent;
  border-color: var(--jd-border);
  transition: background-color 0.15s;
}

:deep(.p-datatable .p-datatable-tbody > tr:hover) {
  background-color: rgba(55, 65, 81, 0.5) !important;
}

:deep(.p-datatable .p-datatable-tbody > tr > td) {
  border-color: var(--jd-border);
  color: var(--jd-text);
  padding: 0.75rem 1rem;
}

:deep(.p-datatable .p-datatable-tbody > tr.p-row-odd) {
  background-color: rgba(31, 41, 55, 0.3);
}

:deep(.p-button.p-button-outlined) {
  border-color: var(--jd-blue);
  color: var(--jd-blue);
}

:deep(.p-button.p-button-outlined:hover) {
  background-color: rgba(59, 130, 246, 0.15);
}

:deep(.p-button.p-button-text) {
  color: var(--jd-text-muted);
}

:deep(.p-paginator) {
  background-color: transparent;
  border-color: var(--jd-border);
  color: var(--jd-text-muted);
}

:deep(.p-paginator .p-paginator-page.p-highlight) {
  background-color: var(--jd-blue);
  color: white;
}
</style>
