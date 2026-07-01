<template>
  <div class="jd-page">
    <!-- Header -->
    <div class="jd-section-header">
      <h1 class="jd-section-title">Watchlist</h1>
      <p style="color: var(--jd-text-muted)">Track your favourite crypto and US stocks with live prices</p>
    </div>

    <!-- Add Symbol Card -->
    <div class="jd-card">
      <div class="jd-card-header">
        <h2 class="jd-card-title">Add Symbol</h2>
      </div>
      <div class="jd-card-body">
        <AutoComplete
          v-model="selectedSuggestion"
          :suggestions="suggestions"
          :loading="searching"
          optionLabel="symbol"
          placeholder="Search stocks or crypto... (AAPL, AMZN, BTC, Ethereum)"
          @complete="onSearch"
          @item-select="onSuggestionSelect"
          :disabled="adding"
          :delay="300"
          class="w-full"
          :panelClass="'watchlist-ac-panel'"
          scrollHeight="400px"
        >
          <template #option="{ option }">
            <div class="flex items-center gap-3 w-full min-w-0 py-0.5">
              <!-- Letter avatar -->
              <div
                class="w-9 h-9 rounded-lg flex items-center justify-center shrink-0 text-sm font-bold text-white select-none"
                :style="{ background: tickerGradient(option.symbol) }"
              >
                {{ option.symbol.replace('/USDT','').charAt(0) }}
              </div>

              <!-- Symbol + company name (clickable → detail) -->
              <div class="flex-1 min-w-0">
                <div class="font-bold text-white text-sm leading-tight">
                  {{ option.symbol.replace('/USDT','') }}
                  <span class="text-xs font-normal" style="color: var(--jd-text-muted); margin-left: 0.25rem;">
                    {{ option.market_type === 'stock' ? 'Stock' : 'Crypto' }}
                  </span>
                </div>
                <div style="color: var(--jd-text-muted);" class="text-xs truncate leading-tight mt-0.5">{{ option.name }}</div>
              </div>

              <!-- Change + Price -->
              <div class="text-right shrink-0 leading-tight mr-2">
                <div
                  v-if="option.change_24h !== null && option.change_24h !== undefined"
                  class="text-xs font-semibold"
                  :class="option.change_24h >= 0 ? 'price-up' : 'price-down'"
                >
                  <span v-if="option.change_dollar !== null && option.change_dollar !== undefined">
                    {{ option.change_dollar >= 0 ? '+' : '' }}${{ Math.abs(option.change_dollar).toFixed(2) }}
                  </span>
                  <span class="ml-1 opacity-80">({{ option.change_24h >= 0 ? '+' : '' }}{{ option.change_24h.toFixed(2) }}%)</span>
                </div>
                <div class="font-mono text-white text-sm font-semibold mt-0.5">
                  {{ option.last ? '$' + formatPrice(option.last) : '--' }}
                </div>
              </div>

              <!-- Inline Add button -->
              <button
                class="shrink-0 jd-btn jd-btn-primary jd-btn-sm"
                :class="{ 'opacity-50 cursor-wait': addingSymbol === option.symbol }"
                @click.stop="addFromDropdown(option)"
                title="Add to watchlist"
              >
                <i v-if="addingSymbol !== option.symbol" class="pi pi-plus text-xs"></i>
                <i v-else class="pi pi-spin pi-spinner text-xs"></i>
              </button>
            </div>
          </template>
        </AutoComplete>
        <p v-if="addError" class="jd-alert error mt-3 mb-0">⚠ {{ addError }}</p>
        <p class="text-xs mt-2" style="color: var(--jd-text-muted);">Click a result to view the chart · click <span style="color: var(--jd-blue);">+</span> to add to watchlist</p>
      </div>
    </div>

    <!-- Watchlist Table -->
    <DataTable
      :columns="columns"
      :data="items"
      :searchable="['symbol']"
      search-placeholder="Search symbols…"
      :page-size="10"
      :loading="loading"
      empty-text="Your watchlist is empty"
    >
      <template #toolbar-left>
        <h2 class="jd-card-title" style="margin-right: auto;">
          Your Watchlist
          <span style="color: var(--jd-text-muted);" class="text-sm font-normal ml-2">({{ items.length }} symbols)</span>
        </h2>
      </template>
      <template #toolbar-right>
        <span v-if="loadingPrices" class="text-sm" style="color: var(--jd-text-muted);">Updating prices...</span>
        <span v-else-if="items.length" class="text-sm" style="color: var(--jd-text-muted);">Updated {{ lastUpdated }}</span>
        <Button
          icon="pi pi-refresh"
          class="p-button-sm p-button-text p-button-rounded"
          :loading="loadingPrices"
          :disabled="items.length === 0"
          @click="loadPrices"
        />
      </template>

      <template #cell:symbol="{ row }">
        <div class="flex items-center gap-2">
          <span class="font-semibold text-white">{{ row.symbol }}</span>
          <span :class="['jd-badge', row.market_type === 'stock' ? 'blue' : 'yellow']">
            {{ row.market_type === 'stock' ? 'US' : 'Crypto' }}
          </span>
        </div>
      </template>

      <template #cell:last="{ row }">
        <span class="font-mono" :style="{ color: row.last ? 'var(--jd-text)' : 'var(--jd-text-muted)' }">
          {{ row.last ? '$' + formatPrice(row.last) : '--' }}
        </span>
      </template>

      <template #cell:change_24h="{ row }">
        <span
          v-if="row.change_24h !== null && row.change_24h !== undefined"
          class="font-semibold"
          :class="row.change_24h >= 0 ? 'price-up' : 'price-down'"
        >
          {{ row.change_24h >= 0 ? '+' : '' }}{{ row.change_24h.toFixed(2) }}%
        </span>
        <span v-else style="color: var(--jd-text-muted);">--</span>
      </template>

      <template #cell:high="{ row }">
        <span style="color: var(--jd-text-muted);" class="font-mono text-sm">
          {{ row.high ? '$' + formatPrice(row.high) : '--' }}
        </span>
      </template>

      <template #cell:low="{ row }">
        <span style="color: var(--jd-text-muted);" class="font-mono text-sm">
          {{ row.low ? '$' + formatPrice(row.low) : '--' }}
        </span>
      </template>

      <template #cell:created_at="{ row }">
        <span style="color: var(--jd-text-muted);" class="text-sm">
          {{ new Date(row.created_at).toLocaleDateString() }}
        </span>
      </template>

      <template #row-actions="{ row }">
        <div class="flex gap-2">
          <router-link :to="`/market/${encodeURIComponent(row.symbol)}`">
            <Button
              icon="pi pi-chart-bar"
              class="p-button-sm p-button-text p-button-rounded"
              v-tooltip="'View Chart'"
            />
          </router-link>
          <Button
            icon="pi pi-trash"
            class="jd-btn jd-btn-danger jd-btn-sm"
            v-tooltip="'Remove'"
            :loading="removingId === row.id"
            @click="removeSymbol(row)"
          />
        </div>
      </template>

      <template #empty>
        <div class="jd-empty">
          <i class="pi pi-star"></i>
          <p>Your watchlist is empty</p>
          <p>Add crypto or US stocks above to start tracking.</p>
        </div>
      </template>
    </DataTable>

    <!-- Alpaca prices missing warning -->
    <div
      v-if="hasMissingStockPrices"
      class="jd-alert warning"
    >
      <span class="mr-2">⚠</span>
      <span>
        US stock prices not loading — Alpaca API key may not be active in Docker.
        Run <code class="bg-gray-800 px-1.5 py-0.5 rounded text-xs font-mono">docker compose up --build -d</code> to reload environment variables.
      </span>
    </div>

    <!-- Error alert -->
    <div v-if="globalError" class="jd-alert error">
      ⚠ {{ globalError }}
    </div>
  </div>

  <Toast />
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import DataTable from '@/components/common/DataTable.vue'
import Button from 'primevue/button'
import AutoComplete from 'primevue/autocomplete'
import Toast from 'primevue/toast'
import { useToast } from 'primevue/usetoast'
import { watchlistApi, marketApi } from '@/api/market'

const router = useRouter()

const toast = useToast()

// ── State ───────────────────────────────────────────────────────
const items = ref([])
const loading = ref(false)
const loadingPrices = ref(false)
const adding = ref(false)
const addingSymbol = ref(null)  // tracks which dropdown symbol is being added
const searching = ref(false)
const removingId = ref(null)
const addError = ref(null)
const globalError = ref(null)
const lastUpdated = ref('--')

// AutoComplete state
const selectedSuggestion = ref(null)   // string or suggestion object
const suggestions = ref([])

// market_type is auto-detected from selected suggestion; default crypto for crypto /USDT normalisation
const detectedMarketType = ref('crypto')

// ── Table columns (reusable DataTable) ──────────────────────────
const columns = [
  { key: 'symbol', header: 'Symbol', sortable: true },
  { key: 'last', header: 'Price', sortable: true, align: 'right' },
  { key: 'change_24h', header: '24h Change', sortable: true, align: 'right' },
  { key: 'high', header: 'Day High', sortable: true, align: 'right' },
  { key: 'low', header: 'Day Low', sortable: true, align: 'right' },
  { key: 'created_at', header: 'Added', sortable: true, align: 'right' },
]

// Show warning banner when stock items exist but have no prices
const hasMissingStockPrices = computed(() =>
  items.value.some(i => i.market_type === 'stock' && !i.last)
)

// ── Ticker avatar gradient ──────────────────────────────────────
const GRADIENTS = [
  'linear-gradient(135deg, #1a56db, #7e3af2)',
  'linear-gradient(135deg, #0e9f6e, #057a55)',
  'linear-gradient(135deg, #d61f69, #9061f9)',
  'linear-gradient(135deg, #ff5a1f, #e3a008)',
  'linear-gradient(135deg, #0694a2, #1c64f2)',
  'linear-gradient(135deg, #7e3af2, #d61f69)',
  'linear-gradient(135deg, #057a55, #0694a2)',
  'linear-gradient(135deg, #1c64f2, #0e9f6e)',
]
function tickerGradient(symbol) {
  const base = symbol.replace('/USDT', '')
  let hash = 0
  for (let i = 0; i < base.length; i++) {
    hash = base.charCodeAt(i) + ((hash << 5) - hash)
  }
  return GRADIENTS[Math.abs(hash) % GRADIENTS.length]
}

// ── Formatters ──────────────────────────────────────────────────
function formatPrice(val) {
  if (!val && val !== 0) return '--'
  if (val >= 1000) return val.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  if (val >= 1) return val.toFixed(4)
  return val.toFixed(6)
}

// ── Load watchlist + prices ─────────────────────────────────────
async function loadWatchlist() {
  loading.value = true
  globalError.value = null
  try {
    const res = await watchlistApi.getDefaultWithPrices()
    items.value = res.data
    lastUpdated.value = new Date().toLocaleTimeString()
  } catch (err) {
    globalError.value = err.response?.data?.detail || 'Failed to load watchlist'
  } finally {
    loading.value = false
  }
}

async function loadPrices() {
  loadingPrices.value = true
  try {
    const res = await watchlistApi.getDefaultWithPrices()
    items.value = res.data
    lastUpdated.value = new Date().toLocaleTimeString()
  } catch (err) {
    // silent
  } finally {
    loadingPrices.value = false
  }
}

// ── AutoComplete ────────────────────────────────────────────────
// Note: debouncing is handled by :delay="300" on the component — no setTimeout needed here.
async function onSearch(event) {
  const q = event.query?.trim()
  if (!q || q.length < 1) {
    suggestions.value = []
    return
  }
  searching.value = true
  try {
    // Always search BOTH stocks + crypto simultaneously
    const [stocksRes, cryptoRes] = await Promise.allSettled([
      marketApi.searchStocks(q),
      marketApi.searchCrypto(q),
    ])
    const stocks  = stocksRes.status  === 'fulfilled' ? (stocksRes.value.data  || []) : []
    const cryptos = cryptoRes.status  === 'fulfilled' ? (cryptoRes.value.data  || []) : []

    // Stocks first (most users search stocks more often)
    suggestions.value = [...stocks, ...cryptos].slice(0, 10)
  } catch {
    suggestions.value = []
  } finally {
    searching.value = false
  }
}

function onSuggestionSelect(event) {
  const option = event.value
  if (!option) return
  // Clicking the row navigates to the market detail page
  const sym = option.symbol
  router.push(`/market/${encodeURIComponent(sym)}`)
  // Clear the input after navigation
  selectedSuggestion.value = null
  suggestions.value = []
}

// Inline "+" button in the dropdown — adds without navigating
async function addFromDropdown(option) {
  let symbol = option.symbol
  if (option.market_type === 'crypto' && !symbol.includes('/')) {
    symbol = `${symbol}/USDT`
  }
  addingSymbol.value = option.symbol
  addError.value = null
  try {
    await watchlistApi.addToDefault({ symbol, market_type: option.market_type })
    toast.add({ severity: 'success', summary: 'Added', detail: `${symbol} added to watchlist`, life: 2000 })
    loadWatchlist()
  } catch (err) {
    if (err.response?.status === 409) {
      toast.add({ severity: 'warn', summary: 'Already in watchlist', detail: symbol, life: 2000 })
    } else {
      addError.value = err.response?.data?.detail || 'Failed to add'
    }
  } finally {
    addingSymbol.value = null
  }
}

// ── Remove symbol ───────────────────────────────────────────────
async function removeSymbol(item) {
  removingId.value = item.id
  try {
    await watchlistApi.removeFromDefault(item.id)
    items.value = items.value.filter(i => i.id !== item.id)
    toast.add({ severity: 'info', summary: 'Removed', detail: `${item.symbol} removed`, life: 2000 })
  } catch (err) {
    toast.add({ severity: 'error', summary: 'Error', detail: err.response?.data?.detail || 'Failed to remove', life: 4000 })
  } finally {
    removingId.value = null
  }
}

onMounted(() => {
  loadWatchlist()
})
</script>

<style scoped>
/* ── Design System Variables ──────────────────────────────────── */
:root {
  --jd-text: #f3f4f6;
  --jd-text-muted: #9ca3af;
  --jd-border: #1f2937;
  --jd-blue: #3b82f6;
  --jd-green: #10b981;
  --jd-red: #ef4444;
  --jd-yellow: #f59e0b;
}

/* ── Page Layout ───────────────────────────────────────────────── */
.jd-page {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

/* ── Section Header ────────────────────────────────────────────── */
.jd-section-header {
  margin-bottom: 0.5rem;
}
.jd-section-title {
  font-size: 1.875rem;
  font-weight: bold;
  color: var(--jd-text);
  margin-bottom: 0.5rem;
}

/* ── Cards ─────────────────────────────────────────────────────── */
.jd-card {
  background-color: rgba(31, 41, 55, 0.6);
  border: 1px solid var(--jd-border);
  border-radius: 0.5rem;
  overflow: hidden;
}
.jd-card-header {
  padding: 1.25rem;
  border-bottom: 1px solid var(--jd-border);
  background-color: rgba(17, 24, 39, 0.5);
}
.jd-card-title {
  font-size: 1.125rem;
  font-weight: bold;
  color: var(--jd-text);
}
.jd-card-body {
  padding: 1.5rem;
}

/* ── Table ─────────────────────────────────────────────────────── */
:deep(.jd-table) {
  background-color: transparent;
  color: var(--jd-text);
}
:deep(.jd-table .p-datatable-thead > tr > th) {
  background-color: rgba(75, 85, 99, 0.4);
  color: var(--jd-text-muted);
  border-color: var(--jd-border);
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  font-weight: 600;
}
:deep(.jd-table .p-datatable-tbody > tr) {
  background-color: transparent;
  border-color: var(--jd-border);
  transition: background-color 0.15s;
}
:deep(.jd-table .p-datatable-tbody > tr:hover) {
  background-color: rgba(55, 65, 81, 0.5) !important;
}
:deep(.jd-table .p-datatable-tbody > tr > td) {
  border-color: var(--jd-border);
  color: var(--jd-text);
  padding: 0.75rem 1rem;
}
:deep(.jd-table .p-datatable-tbody > tr.p-row-odd) {
  background-color: rgba(31, 41, 55, 0.3);
}

/* ── Badges ────────────────────────────────────────────────────── */
.jd-badge {
  display: inline-block;
  font-size: 0.75rem;
  padding: 0.375rem 0.75rem;
  border-radius: 9999px;
  font-weight: 500;
  text-align: center;
}
.jd-badge.green {
  background-color: rgba(16, 185, 129, 0.2);
  color: #10b981;
}
.jd-badge.red {
  background-color: rgba(239, 68, 68, 0.2);
  color: #ef4444;
}
.jd-badge.blue {
  background-color: rgba(59, 130, 246, 0.2);
  color: #60a5fa;
}
.jd-badge.yellow {
  background-color: rgba(245, 158, 11, 0.2);
  color: #fbbf24;
}
.jd-badge.gray {
  background-color: rgba(107, 114, 128, 0.2);
  color: #d1d5db;
}

/* ── Buttons ───────────────────────────────────────────────────── */
.jd-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  padding: 0.5rem 1rem;
  border-radius: 0.375rem;
  font-weight: 500;
  border: none;
  cursor: pointer;
  transition: all 0.15s ease;
}
.jd-btn-primary {
  background-color: var(--jd-blue);
  color: white;
}
.jd-btn-primary:hover:not(:disabled) {
  background-color: #2563eb;
}
.jd-btn-danger {
  background-color: rgba(239, 68, 68, 0.2);
  color: var(--jd-red);
  border: 1px solid var(--jd-red);
}
.jd-btn-danger:hover:not(:disabled) {
  background-color: rgba(239, 68, 68, 0.3);
}
.jd-btn-ghost {
  background-color: transparent;
  color: var(--jd-text);
  border: 1px solid var(--jd-border);
}
.jd-btn-ghost:hover:not(:disabled) {
  background-color: rgba(75, 85, 99, 0.4);
}
.jd-btn-sm {
  padding: 0.375rem 0.75rem;
  font-size: 0.875rem;
}
.jd-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* ── Empty State ───────────────────────────────────────────────── */
.jd-empty {
  text-align: center;
  padding: 3rem 1.5rem;
}
.jd-empty i {
  font-size: 3rem;
  color: rgba(107, 114, 128, 0.5);
  display: block;
  margin-bottom: 1rem;
}
.jd-empty p {
  margin: 0.5rem 0;
  color: var(--jd-text-muted);
}
.jd-empty p:first-of-type {
  font-size: 1.125rem;
  font-weight: 500;
  color: var(--jd-text);
}

/* ── Alerts ────────────────────────────────────────────────────── */
.jd-alert {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  padding: 0.75rem 1rem;
  border-radius: 0.375rem;
  font-size: 0.875rem;
  margin: 0;
}
.jd-alert.error {
  background-color: rgba(239, 68, 68, 0.1);
  border: 1px solid var(--jd-red);
  color: #fca5a5;
}
.jd-alert.warning {
  background-color: rgba(245, 158, 11, 0.1);
  border: 1px solid var(--jd-yellow);
  color: #fcd34d;
}

/* ── AutoComplete input ────────────────────────────────────────── */
:deep(.p-autocomplete) {
  width: 100%;
}
:deep(.p-autocomplete .p-inputtext) {
  width: 100%;
  background-color: rgba(55, 65, 81, 0.6);
  border-color: var(--jd-border);
  color: var(--jd-text);
}
:deep(.p-autocomplete .p-inputtext:focus) {
  border-color: var(--jd-blue);
  box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.25);
}
:deep(.p-autocomplete .p-inputtext::placeholder) {
  color: var(--jd-text-muted);
}
:deep(.p-autocomplete-loader) {
  color: var(--jd-blue);
}

/* ── Price Changes ─────────────────────────────────────────────── */
.price-up {
  color: var(--jd-green);
}
.price-down {
  color: var(--jd-red);
}
</style>

<!-- Global styles for PrimeVue teleported overlays (AutoComplete panel) -->
<style>
.watchlist-ac-panel.p-autocomplete-overlay {
  background-color: rgba(17, 24, 39, 0.95) !important;
  border: 1px solid #1f2937 !important;
  border-radius: 0.5rem !important;
  box-shadow: 0 12px 28px rgba(0, 0, 0, 0.7) !important;
}
.watchlist-ac-panel .p-autocomplete-list {
  padding: 4px !important;
  gap: 1px;
}
.watchlist-ac-panel .p-autocomplete-option {
  color: #f3f4f6 !important;
  border-radius: 6px !important;
  padding: 0.55rem 0.75rem !important;
  transition: background 0.1s;
}
.watchlist-ac-panel .p-autocomplete-option:hover,
.watchlist-ac-panel .p-autocomplete-option.p-focus {
  background-color: rgba(59, 130, 246, 0.18) !important;
  color: #fff !important;
}
</style>
