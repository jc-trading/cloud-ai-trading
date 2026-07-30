<template>
  <div class="jd-page">
    <!-- Back + Header -->
    <div>
      <button @click="$router.back()" class="back-btn">
        <i class="pi pi-arrow-left"></i> Back
      </button>
      <div class="header-flex">
        <div class="header-icon" :style="{ background: tickerGradient(symbol) }">
          {{ symbol.replace('/USDT','').charAt(0) }}
        </div>
        <div>
          <h1 class="text-2xl font-bold">{{ symbol.replace('/USDT','') }}</h1>
          <span class="text-sm" style="color: var(--jd-text-muted)">{{ isStock ? 'US Stock' : 'Crypto · USDT pair' }}</span>
        </div>
        <div class="header-actions">
          <button
            class="jd-btn jd-btn-ghost"
            :disabled="addingToWatchlist"
            @click="toggleWatchlist"
          >
            <i :class="addingToWatchlist ? 'pi pi-spin pi-spinner' : (inWatchlist ? 'pi pi-heart-fill' : 'pi pi-heart')"></i>
            {{ inWatchlist ? 'In Watchlist' : 'Add to Watchlist' }}
          </button>
          <button class="jd-btn jd-btn-primary" @click="$router.push('/sim')">
            <i class="pi pi-arrow-right"></i>
            Practice trade
          </button>
        </div>
      </div>
    </div>

    <!-- Loading skeleton -->
    <div v-if="loading" class="loading-skeleton">
      <div class="skeleton-grid">
        <div v-for="i in 4" :key="i" class="skeleton-card" />
      </div>
      <div class="skeleton-chart" />
    </div>

    <template v-else-if="ticker">
      <!-- Price Stats -->
      <div class="stats-grid">
        <div class="jd-stat-card" style="--accent: var(--jd-blue)">
          <div class="jd-stat-label">Price</div>
          <div class="jd-stat-value font-mono">${{ formatPrice(ticker.last) }}</div>
        </div>
        <div class="jd-stat-card" :style="{ '--accent': ticker.change_24h >= 0 ? 'var(--jd-green)' : 'var(--jd-red)' }">
          <div class="jd-stat-label">24h Change</div>
          <div class="jd-stat-value" :class="ticker.change_24h >= 0 ? 'price-up' : 'price-down'">
            {{ ticker.change_24h >= 0 ? '+' : '' }}{{ ticker.change_24h?.toFixed(2) ?? '--' }}%
          </div>
        </div>
        <div class="jd-stat-card" style="--accent: var(--jd-blue)">
          <div class="jd-stat-label">Day High / Low</div>
          <div class="jd-stat-value font-mono">
            <span class="price-up">${{ formatPrice(ticker.high) }}</span>
            <span style="color: var(--jd-text-muted)"> / </span>
            <span class="price-down">${{ formatPrice(ticker.low) }}</span>
          </div>
        </div>
        <div class="jd-stat-card" style="--accent: var(--jd-blue)">
          <div class="jd-stat-label">Volume</div>
          <div class="jd-stat-value font-mono">{{ formatVolume(ticker.volume) }}</div>
        </div>
      </div>

      <!-- Chart Card -->
      <div class="jd-card">
        <div class="jd-card-header flex-header">
          <h2 class="jd-card-title">Price Chart</h2>
          <div class="jd-tabs">
            <button
              v-for="tf in timeframes"
              :key="tf.value"
              @click="changeTimeframe(tf.value)"
              class="jd-tab"
              :class="{ active: interval === tf.value }"
            >{{ tf.label }}</button>
          </div>
        </div>
        <div class="jd-card-body">
          <div v-if="loadingCandles" class="chart-loading">
            <i class="pi pi-spin pi-spinner"></i>
          </div>
          <div v-else-if="candles.length === 0" class="chart-empty">
            <p>No candle data available for this timeframe.</p>
          </div>
          <div v-else ref="chartContainer" class="chart-container"></div>
        </div>
      </div>

      <!-- Info Grid -->
      <div class="info-grid">
        <!-- Market Info -->
        <div class="jd-card">
          <div class="jd-card-header">
            <h3 class="jd-card-title">Market Info</h3>
          </div>
          <div class="jd-card-body">
            <div class="info-row">
              <span class="info-label">Symbol</span>
              <span class="font-mono font-semibold">{{ symbol }}</span>
            </div>
            <div class="info-row">
              <span class="info-label">Type</span>
              <span>{{ isStock ? '🇺🇸 US Stock' : '🔶 Crypto' }}</span>
            </div>
            <div class="info-row">
              <span class="info-label">Bid</span>
              <span class="font-mono">{{ ticker.bid ? '$' + formatPrice(ticker.bid) : '--' }}</span>
            </div>
            <div class="info-row">
              <span class="info-label">Ask</span>
              <span class="font-mono">{{ ticker.ask ? '$' + formatPrice(ticker.ask) : '--' }}</span>
            </div>
            <div class="info-row">
              <span class="info-label">Day Volume</span>
              <span class="font-mono">{{ formatVolume(ticker.volume) }}</span>
            </div>
          </div>
        </div>

        <!-- Quick Actions -->
        <div class="jd-card">
          <div class="jd-card-header">
            <h3 class="jd-card-title">Quick Actions</h3>
          </div>
          <div class="jd-card-body">
            <div class="space-y-3">
              <button
                :class="['jd-btn w-full', inWatchlist ? 'jd-btn-danger' : 'jd-btn-ghost']"
                :disabled="addingToWatchlist"
                @click="toggleWatchlist"
              >
                <i :class="addingToWatchlist ? 'pi pi-spin pi-spinner' : (inWatchlist ? 'pi pi-heart-fill' : 'pi pi-heart')"></i>
                {{ inWatchlist ? 'Remove from Watchlist' : 'Add to Watchlist' }}
              </button>
              <button class="jd-btn jd-btn-primary w-full" @click="$router.push('/sim')">Practice trade →</button>
            </div>
            <div v-if="watchlistMsg" class="watchlist-msg" :class="watchlistMsg.type === 'success' ? 'success' : 'error'">
              {{ watchlistMsg.text }}
            </div>
          </div>
        </div>
      </div>
    </template>

    <!-- Error state -->
    <div v-else-if="error" class="jd-empty error-state">
      <i class="pi pi-exclamation-circle"></i>
      <p>Failed to load {{ symbol }}</p>
      <p class="text-sm" style="color: var(--jd-text-muted)">{{ error }}</p>
      <button class="jd-btn jd-btn-primary mt-4" @click="loadData">
        <i class="pi pi-refresh"></i>
        Retry
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { useToast } from '@/composables/useToast'
import { CandlestickSeries, createChart } from 'lightweight-charts'
import { marketApi, watchlistApi } from '@/api/market'

const props = defineProps({ symbol: { type: String, required: true } })
const toast = useToast()

// ── State ────────────────────────────────────────────────────────
const ticker        = ref(null)
const candles       = ref([])
const loading       = ref(false)
const loadingCandles = ref(false)
const error         = ref(null)
const interval      = ref('1h')
const chartContainer = ref(null)
const inWatchlist   = ref(false)
const addingToWatchlist = ref(false)
const watchlistMsg  = ref(null)

let chart   = null
let candleSeries = null

const isStock = computed(() => !props.symbol.includes('/'))

const timeframes = [
  { label: '1m',  value: '1m' },
  { label: '5m',  value: '5m' },
  { label: '15m', value: '15m' },
  { label: '1H',  value: '1h' },
  { label: '4H',  value: '4h' },
  { label: '1D',  value: '1d' },
]

// ── Formatters ───────────────────────────────────────────────────
const GRADIENTS = [
  'linear-gradient(135deg,#1a56db,#7e3af2)',
  'linear-gradient(135deg,#0e9f6e,#057a55)',
  'linear-gradient(135deg,#d61f69,#9061f9)',
  'linear-gradient(135deg,#ff5a1f,#e3a008)',
  'linear-gradient(135deg,#0694a2,#1c64f2)',
  'linear-gradient(135deg,#7e3af2,#d61f69)',
  'linear-gradient(135deg,#057a55,#0694a2)',
  'linear-gradient(135deg,#1c64f2,#0e9f6e)',
]
function tickerGradient(sym) {
  const base = sym.replace('/USDT', '')
  let hash = 0
  for (let i = 0; i < base.length; i++) hash = base.charCodeAt(i) + ((hash << 5) - hash)
  return GRADIENTS[Math.abs(hash) % GRADIENTS.length]
}

function formatPrice(val) {
  if (!val) return '--'
  if (val >= 1000) return val.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  if (val >= 1)    return val.toFixed(4)
  return val.toFixed(6)
}

function formatVolume(val) {
  if (!val) return '--'
  if (val >= 1e9) return (val / 1e9).toFixed(2) + 'B'
  if (val >= 1e6) return (val / 1e6).toFixed(2) + 'M'
  if (val >= 1e3) return (val / 1e3).toFixed(1) + 'K'
  return val.toFixed(0)
}

// ── Data loading ─────────────────────────────────────────────────
async function loadData() {
  loading.value = true
  error.value   = null
  try {
    const res = await marketApi.getSymbol(props.symbol)
    ticker.value = res.data
  } catch (e) {
    error.value = e.response?.data?.detail || e.message || 'Network error'
  } finally {
    loading.value = false
  }
  await loadCandles()
}

async function loadCandles() {
  loadingCandles.value = true
  try {
    const res = await marketApi.getCandles(props.symbol, { interval: interval.value, limit: 200 })
    candles.value = res.data || []
    loadingCandles.value = false      // mount the container BEFORE drawing
    await nextTick()
    renderChart()
  } catch (e) {
    console.error('candle load/render failed', e)
    candles.value = []
  } finally {
    loadingCandles.value = false
  }
}

async function changeTimeframe(tf) {
  interval.value = tf
  await loadCandles()
}

// ── Chart ────────────────────────────────────────────────────────
function renderChart() {
  if (!chartContainer.value || candles.value.length === 0) return

  // Destroy previous instance
  if (chart) {
    chart.remove()
    chart = null
    candleSeries = null
  }

  chart = createChart(chartContainer.value, {
    layout: {
      background: { color: 'transparent' },
      textColor:  '#9ca3af',
    },
    grid: {
      vertLines:  { color: 'rgba(55,65,81,0.5)' },
      horzLines:  { color: 'rgba(55,65,81,0.5)' },
    },
    crosshair: { mode: 1 },
    rightPriceScale: { borderColor: '#374151' },
    timeScale: {
      borderColor: '#374151',
      timeVisible: true,
      secondsVisible: false,
    },
    width:  chartContainer.value.clientWidth,
    height: 320,
  })

  candleSeries = chart.addSeries(CandlestickSeries, {
    upColor:          '#22c55e',
    downColor:        '#ef4444',
    borderUpColor:    '#22c55e',
    borderDownColor:  '#ef4444',
    wickUpColor:      '#22c55e',
    wickDownColor:    '#ef4444',
  })

  const data = candles.value.map(c => ({
    time:  Math.floor(c.timestamp / 1000),
    open:  c.open,
    high:  c.high,
    low:   c.low,
    close: c.close,
  }))
  candleSeries.setData(data)
  chart.timeScale().fitContent()
}

// Resize chart on window resize
function onResize() {
  if (chart && chartContainer.value) {
    chart.applyOptions({ width: chartContainer.value.clientWidth })
  }
}

// ── Watchlist toggle ─────────────────────────────────────────────
async function checkWatchlist() {
  try {
    const res = await watchlistApi.getDefaultWithPrices()
    inWatchlist.value = (res.data || []).some(i => i.symbol === props.symbol)
  } catch { /* silent */ }
}

async function toggleWatchlist() {
  addingToWatchlist.value = true
  watchlistMsg.value = null
  try {
    if (inWatchlist.value) {
      // Find the item id and remove it
      const res = await watchlistApi.getDefaultWithPrices()
      const item = (res.data || []).find(i => i.symbol === props.symbol)
      if (item) await watchlistApi.removeFromDefault(item.id)
      inWatchlist.value = false
      watchlistMsg.value = { type: 'success', text: `${props.symbol} removed from watchlist` }
    } else {
      const market_type = isStock.value ? 'stock' : 'crypto'
      await watchlistApi.addToDefault({ symbol: props.symbol, market_type })
      inWatchlist.value = true
      watchlistMsg.value = { type: 'success', text: `${props.symbol} added to watchlist ✓` }
    }
  } catch (e) {
    watchlistMsg.value = { type: 'error', text: e.response?.data?.detail || 'Failed' }
  } finally {
    addingToWatchlist.value = false
  }
}

// ── Lifecycle ────────────────────────────────────────────────────
onMounted(async () => {
  window.addEventListener('resize', onResize)
  await loadData()
  await checkWatchlist()
})

onUnmounted(() => {
  window.removeEventListener('resize', onResize)
  if (chart) { chart.remove(); chart = null }
})

watch(() => props.symbol, async () => {
  if (chart) { chart.remove(); chart = null }
  await loadData()
  await checkWatchlist()
})
</script>

<style scoped>
a { text-decoration: none; }

.back-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 0.875rem;
  color: var(--jd-blue);
  background: none;
  border: none;
  cursor: pointer;
  margin-bottom: 16px;
  transition: opacity 0.2s;
}

.back-btn:hover {
  opacity: 0.8;
}

.header-flex {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-top: 8px;
}

.header-icon {
  width: 48px;
  height: 48px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  font-weight: bold;
  color: white;
  flex-shrink: 0;
}

.header-actions {
  display: flex;
  gap: 8px;
  margin-left: auto;
}

.loading-skeleton {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.skeleton-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 20px;
}

.skeleton-card,
.skeleton-chart {
  height: 96px;
  background: rgba(75, 85, 99, 0.2);
  border-radius: 8px;
  animation: pulse 2s infinite;
}

.skeleton-chart {
  height: 384px;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

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

.chart-loading,
.chart-empty {
  height: 320px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--jd-text-muted);
}

.chart-loading i {
  font-size: 24px;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.chart-container {
  width: 100%;
  height: 320px;
}

.info-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 20px;
}

.info-row {
  display: flex;
  justify-content: space-between;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--jd-border);
}

.info-row:last-child {
  border-bottom: none;
  padding-bottom: 0;
}

.info-label {
  color: var(--jd-text-muted);
  font-size: 0.875rem;
}

.space-y-3 > * + * {
  margin-top: 12px;
}

.mt-4 {
  margin-top: 16px;
}

.w-full {
  width: 100%;
}

.watchlist-msg {
  margin-top: 12px;
  text-align: center;
  font-size: 0.75rem;
  padding: 8px;
  border-radius: 4px;
}

.watchlist-msg.success {
  color: var(--jd-green);
  background: rgba(34, 197, 94, 0.1);
}

.watchlist-msg.error {
  color: var(--jd-red);
  background: rgba(239, 68, 68, 0.1);
}

.error-state {
  padding: 80px 40px;
}

.error-state i {
  font-size: 48px;
  color: var(--jd-red);
  margin-bottom: 16px;
}

.error-state p {
  margin: 8px 0;
}

.error-state p:first-of-type {
  font-size: 18px;
  color: var(--jd-red);
  font-weight: 500;
}

.font-mono {
  font-family: 'Courier New', monospace;
}
</style>
