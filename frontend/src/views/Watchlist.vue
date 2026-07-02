<template>
  <div class="jd-page">
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
          <span style="color: var(--jd-text-muted);" class="jd-mono" >&nbsp;({{ items.length }})</span>
        </h2>
      </template>
      <template #toolbar-right>
        <button class="jd-btn jd-btn-primary jd-btn-sm" @click="openAdd">
          <i class="pi pi-plus"></i> Add Symbol
        </button>
        <span v-if="loadingPrices" class="jd-mono" style="color: var(--jd-text-muted); font-size:11px;">Updating…</span>
        <span v-else-if="items.length" class="jd-mono" style="color: var(--jd-text-muted); font-size:11px;">Updated {{ lastUpdated }}</span>
        <button
          class="jd-btn jd-btn-ghost jd-btn-sm"
          title="Refresh prices"
          :disabled="items.length === 0 || loadingPrices"
          @click="loadPrices"
        >
          <i class="pi pi-refresh" :class="{ 'pi-spin': loadingPrices }"></i>
        </button>
      </template>

      <template #cell:symbol="{ row }">
        <div class="flex items-center gap-2">
          <span class="font-semibold" style="color:#fff">{{ row.symbol }}</span>
          <span :class="['jd-badge', row.market_type === 'stock' ? 'blue' : 'yellow']">
            {{ row.market_type === 'stock' ? 'US' : 'Crypto' }}
          </span>
        </div>
      </template>

      <template #cell:last="{ row }">
        <span :style="{ color: row.last ? 'var(--jd-text)' : 'var(--jd-text-muted)' }">
          {{ row.last ? '$' + formatPrice(row.last) : '—' }}
        </span>
      </template>

      <template #cell:change_24h="{ row }">
        <span
          v-if="row.change_24h !== null && row.change_24h !== undefined"
          :class="row.change_24h >= 0 ? 'price-up' : 'price-down'"
        >
          {{ row.change_24h >= 0 ? '+' : '' }}{{ row.change_24h.toFixed(2) }}%
        </span>
        <span v-else style="color: var(--jd-text-muted);">—</span>
      </template>

      <template #cell:high="{ row }">
        <span style="color: var(--jd-text-muted);">{{ row.high ? '$' + formatPrice(row.high) : '—' }}</span>
      </template>

      <template #cell:low="{ row }">
        <span style="color: var(--jd-text-muted);">{{ row.low ? '$' + formatPrice(row.low) : '—' }}</span>
      </template>

      <template #cell:created_at="{ row }">
        <span style="color: var(--jd-text-muted);">{{ new Date(row.created_at).toLocaleDateString() }}</span>
      </template>

      <template #row-actions="{ row }">
        <div class="flex gap-2">
          <router-link :to="`/market/${encodeURIComponent(row.symbol)}`">
            <button class="jd-btn jd-btn-ghost jd-btn-sm" title="View Chart"><i class="pi pi-chart-bar"></i></button>
          </router-link>
          <button
            class="jd-btn jd-btn-danger jd-btn-sm"
            title="Remove"
            :disabled="removingId === row.id"
            @click="removeSymbol(row)"
          >
            <i class="pi" :class="removingId === row.id ? 'pi-spin pi-spinner' : 'pi-trash'"></i>
          </button>
        </div>
      </template>

      <template #empty>
        <div class="jd-empty">
          <i class="pi pi-star"></i>
          <p>Your watchlist is empty</p>
          <p style="font-size:13px">Click <b>Add Symbol</b> to track crypto or US stocks.</p>
        </div>
      </template>
    </DataTable>

    <!-- Missing-price notice (accurate: names the symbols; only a key/config hint if ALL stocks fail) -->
    <div v-if="allStocksMissing" class="jd-alert warning">
      <i class="pi pi-exclamation-triangle"></i>
      <span>No US stock prices are loading. The market data key may be missing in the backend — check
        <code style="background:var(--jd-input);font-family:var(--jd-mono);padding:1px 6px;border-radius:5px">FINNHUB_API_KEY</code>
        / Alpaca keys in the container env.</span>
    </div>
    <div v-else-if="missingStockSymbols.length" class="jd-alert warning">
      <i class="pi pi-info-circle"></i>
      <span>No live price for {{ missingStockSymbols.join(', ') }} — the symbol may be unsupported or
        temporarily rate-limited (check the ticker; e.g. Tempus AI is <b>TEM</b>, not TEMPUS). Other prices are live.</span>
    </div>

    <!-- Error alert -->
    <div v-if="globalError" class="jd-alert error"><i class="pi pi-times-circle"></i><span>{{ globalError }}</span></div>

    <!-- Add Symbol modal -->
    <Modal v-model="showAdd" title="Add Symbol" width="560px">
      <label class="jd-search add-search">
        <i class="pi pi-search ic"></i>
        <input
          ref="searchInput"
          v-model="q"
          @input="onSearch"
          placeholder="Search stocks or crypto… (AAPL, AMZN, BTC, Ethereum)"
        />
      </label>

      <div class="add-results">
        <div v-if="searching" class="add-hint"><i class="pi pi-spin pi-spinner"></i> Searching…</div>
        <ul v-else-if="suggestions.length" class="add-list">
          <li v-for="option in suggestions" :key="option.symbol" @click="pick(option)">
            <div class="ava" :style="{ background: tickerGradient(option.symbol) }">
              {{ option.symbol.replace('/USDT','').charAt(0) }}
            </div>
            <div class="meta">
              <div class="sym">
                {{ option.symbol.replace('/USDT','') }}
                <span class="tag">{{ option.market_type === 'stock' ? 'Stock' : 'Crypto' }}</span>
              </div>
              <div class="name">{{ option.name }}</div>
            </div>
            <div class="px" v-if="option.last">
              <div v-if="option.change_24h != null" class="chg" :class="option.change_24h >= 0 ? 'price-up' : 'price-down'">
                {{ option.change_24h >= 0 ? '+' : '' }}{{ option.change_24h.toFixed(2) }}%
              </div>
              <div class="val">${{ formatPrice(option.last) }}</div>
            </div>
            <span v-if="isAdded(option)" class="added-tick" title="Already in watchlist" @click.stop>
              <i class="pi pi-check"></i>
            </span>
            <button
              v-else
              class="jd-btn jd-btn-primary jd-btn-sm"
              :disabled="addingSymbol === option.symbol"
              @click.stop="addFromDropdown(option)"
              title="Add to watchlist"
            >
              <i :class="addingSymbol === option.symbol ? 'pi pi-spin pi-spinner' : 'pi pi-plus'"></i>
            </button>
          </li>
        </ul>
        <div v-else-if="q.trim()" class="add-hint">No matches for “{{ q.trim() }}”.</div>
        <div v-else class="add-hint">Type a symbol or company name to search crypto &amp; US stocks.</div>
      </div>

      <p v-if="addError" class="jd-alert error" style="margin-top:12px"><i class="pi pi-times-circle"></i><span>{{ addError }}</span></p>

      <template #footer>
        <span class="jd-mono" style="color:var(--jd-text-faint);font-size:11px;margin-right:auto">
          Click a result to open its chart · <i class="pi pi-plus" style="font-size:10px"></i> to add
        </span>
        <button class="jd-btn jd-btn-ghost jd-btn-sm" @click="showAdd = false">Done</button>
      </template>
    </Modal>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import DataTable from '@/components/common/DataTable.vue'
import Modal from '@/components/common/Modal.vue'
import { useToast } from '@/composables/useToast'
import { watchlistApi, marketApi } from '@/api/market'

const router = useRouter()
const toast = useToast()

// ── State ───────────────────────────────────────────────────────
const items = ref([])
const loading = ref(false)
const loadingPrices = ref(false)
const addingSymbol = ref(null)
const searching = ref(false)
const removingId = ref(null)
const addError = ref(null)
const globalError = ref(null)
const lastUpdated = ref('—')

const showAdd = ref(false)
const searchInput = ref(null)
const q = ref('')
const suggestions = ref([])

const columns = [
  { key: 'symbol', header: 'Symbol', sortable: true },
  { key: 'last', header: 'Price', sortable: true, align: 'right' },
  { key: 'change_24h', header: '24h Change', sortable: true, align: 'right' },
  { key: 'high', header: 'Day High', sortable: true, align: 'right' },
  { key: 'low', header: 'Day Low', sortable: true, align: 'right' },
  { key: 'created_at', header: 'Added', sortable: true, align: 'right' },
]

// Accurate missing-price detection: name the offenders; only blame config when
// EVERY stock lacks a price (a real key issue), not when one ticker is unknown.
const missingStockSymbols = computed(() =>
  items.value.filter(i => i.market_type === 'stock' && !i.last).map(i => i.symbol)
)
const allStocksMissing = computed(() => {
  const stocks = items.value.filter(i => i.market_type === 'stock')
  return stocks.length > 0 && stocks.every(i => !i.last)
})

// Hide search results that are already in the watchlist. Mirror the crypto
// symbol normalization used when adding (BTC -> BTC/USDT) so the match is exact.
function normalizeSym(option) {
  let s = option.symbol
  if (option.market_type === 'crypto' && !s.includes('/')) s = `${s}/USDT`
  return s.toUpperCase()
}
const existingSymbols = computed(() => new Set(items.value.map(i => String(i.symbol).toUpperCase())))
const isAdded = (option) => existingSymbols.value.has(normalizeSym(option))

// ── Ticker avatar gradient ──────────────────────────────────────
const GRADIENTS = [
  'linear-gradient(135deg, #3fe0ff, #a06bff)',
  'linear-gradient(135deg, #2ee08a, #0694a2)',
  'linear-gradient(135deg, #a06bff, #d61f69)',
  'linear-gradient(135deg, #ffc24b, #ff5a1f)',
  'linear-gradient(135deg, #0694a2, #3fe0ff)',
  'linear-gradient(135deg, #7e3af2, #a06bff)',
]
function tickerGradient(symbol) {
  const base = symbol.replace('/USDT', '')
  let hash = 0
  for (let i = 0; i < base.length; i++) hash = base.charCodeAt(i) + ((hash << 5) - hash)
  return GRADIENTS[Math.abs(hash) % GRADIENTS.length]
}

function formatPrice(val) {
  if (!val && val !== 0) return '—'
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
  } catch { /* silent */ } finally {
    loadingPrices.value = false
  }
}

// ── Add-symbol modal ────────────────────────────────────────────
function openAdd() {
  showAdd.value = true
  q.value = ''
  suggestions.value = []
  addError.value = null
  nextTick(() => searchInput.value?.focus())
}

async function onSearch() {
  const query = q.value?.trim()
  if (!query) { suggestions.value = []; return }
  searching.value = true
  try {
    const [stocksRes, cryptoRes] = await Promise.allSettled([
      marketApi.searchStocks(query),
      marketApi.searchCrypto(query),
    ])
    const stocks = stocksRes.status === 'fulfilled' ? (stocksRes.value.data || []) : []
    const cryptos = cryptoRes.status === 'fulfilled' ? (cryptoRes.value.data || []) : []
    suggestions.value = [...stocks, ...cryptos].slice(0, 12)
  } catch {
    suggestions.value = []
  } finally {
    searching.value = false
  }
}

// Clicking a result row navigates to its chart.
function pick(option) {
  if (!option) return
  showAdd.value = false
  router.push(`/market/${encodeURIComponent(option.symbol)}`)
}

// The "+" adds without navigating; keeps the modal open to add more.
async function addFromDropdown(option) {
  let symbol = option.symbol
  if (option.market_type === 'crypto' && !symbol.includes('/')) symbol = `${symbol}/USDT`
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

onMounted(loadWatchlist)
</script>

<style scoped>
/* Watchlist inherits the global .jd-* Oscilloscope system — only the add-modal
   result list needs local styling. */
.add-search { width: 100%; }
.add-results { margin-top: 14px; min-height: 120px; max-height: 340px; overflow-y: auto; }
.add-hint { color: var(--jd-text-muted); font-size: 13px; padding: 24px 4px; text-align: center; }
.add-list { list-style: none; display: flex; flex-direction: column; gap: 2px; }
.add-list li {
  display: flex; align-items: center; gap: 12px;
  padding: 9px 10px; border-radius: 10px; cursor: pointer;
  transition: background var(--jd-trans);
}
.add-list li:hover { background: rgba(63, 224, 255, 0.06); }
.add-list .ava {
  width: 36px; height: 36px; border-radius: 9px; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  font-weight: 700; color: #04121a; font-size: 15px;
}
.add-list .meta { flex: 1; min-width: 0; }
.add-list .sym { font-weight: 600; color: #fff; font-size: 14px; }
.add-list .sym .tag { font-family: var(--jd-mono); font-size: 10px; color: var(--jd-text-muted); margin-left: 6px; text-transform: uppercase; letter-spacing: 0.06em; }
.add-list .name { color: var(--jd-text-muted); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.add-list .px { text-align: right; flex-shrink: 0; font-family: var(--jd-mono); }
.add-list .px .chg { font-size: 11px; font-weight: 600; }
.add-list .px .val { color: #fff; font-size: 13px; font-weight: 600; }
.add-list .added-tick {
  width: 30px; height: 30px; flex-shrink: 0; border-radius: 8px;
  display: inline-flex; align-items: center; justify-content: center;
  color: var(--jd-green); background: var(--jd-green-glow);
  border: 1px solid rgba(46, 224, 138, 0.3); font-size: 13px; cursor: default;
}
.price-up { color: var(--jd-green); }
.price-down { color: var(--jd-red); }
</style>
