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
          <div class="chart-controls">
            <div class="jd-tabs">
              <button
                v-for="tf in TIMEFRAMES"
                :key="tf.value"
                @click="changeTimeframe(tf.value)"
                class="jd-tab"
                :class="{ active: interval === tf.value }"
              >{{ tf.label }}</button>
            </div>
            <div class="indicator-menu">
              <button class="jd-btn jd-btn-ghost indicator-trigger" @click.stop="indicatorMenuOpen = !indicatorMenuOpen">
                <i class="pi pi-sliders-h"></i>
                Indicators
              </button>
              <div v-if="indicatorMenuOpen" class="indicator-dropdown" @click.stop>
                <button
                  v-for="ind in INDICATORS"
                  :key="ind.name"
                  class="indicator-option"
                  :class="{ active: activeIndicators.includes(ind.name) }"
                  @click="toggleIndicator(ind.name)"
                >
                  <i :class="activeIndicators.includes(ind.name) ? 'pi pi-check-square' : 'pi pi-stop'"></i>
                  <span class="indicator-name">{{ ind.label }}</span>
                  <span class="indicator-pane">{{ ind.pane === 'main' ? 'overlay' : 'panel' }}</span>
                </button>
              </div>
            </div>
          </div>
        </div>
        <div class="jd-card-body chart-body">
          <div class="draw-toolbar">
            <button
              v-for="tool in DRAW_TOOLS"
              :key="tool.name"
              class="draw-tool"
              :class="{ active: activeTool === tool.name }"
              :title="tool.label"
              @click="selectTool(tool.name)"
            >
              <i :class="[tool.icon, tool.rotate ? 'rot90' : '']"></i>
            </button>
            <button class="draw-tool danger" title="Clear all drawings" @click="clearDrawings">
              <i class="pi pi-trash"></i>
            </button>
          </div>
          <div class="chart-wrap">
            <div ref="chartContainer" class="chart-container"></div>
            <div v-if="loadingCandles" class="chart-msg">
              <i class="pi pi-spin pi-spinner"></i>
            </div>
            <div v-else-if="noData" class="chart-msg">
              <p>No candle data available for this timeframe.</p>
            </div>
          </div>
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
import { dispose, init } from 'klinecharts'
import { marketApi, watchlistApi } from '@/api/market'

const props = defineProps({ symbol: { type: String, required: true } })
const toast = useToast()

// ── State ────────────────────────────────────────────────────────
const ticker        = ref(null)
const loading       = ref(false)
const loadingCandles = ref(false)
const noData        = ref(false)
const error         = ref(null)
const interval      = ref('1h')
const chartContainer = ref(null)
const activeTool    = ref(null)
const activeIndicators   = ref([])
const indicatorMenuOpen  = ref(false)
const inWatchlist   = ref(false)
const addingToWatchlist = ref(false)
const watchlistMsg  = ref(null)

let chart = null
let pendingOverlayId = null
let restored = false

const isStock = computed(() => !props.symbol.includes('/'))

const TIMEFRAMES = [
  { label: '1m',  value: '1m',  period: { type: 'minute', span: 1 } },
  { label: '5m',  value: '5m',  period: { type: 'minute', span: 5 } },
  { label: '15m', value: '15m', period: { type: 'minute', span: 15 } },
  { label: '1H',  value: '1h',  period: { type: 'hour', span: 1 } },
  { label: '1D',  value: '1d',  period: { type: 'day', span: 1 } },
]

// KLineChart built-in overlays — the drawing toolbar just starts one of them.
const DRAW_TOOLS = [
  { name: 'horizontalStraightLine', label: 'Horizontal line', icon: 'pi pi-minus' },
  { name: 'verticalStraightLine',   label: 'Vertical line',   icon: 'pi pi-minus', rotate: true },
  { name: 'segment',                label: 'Trend line',      icon: 'pi pi-arrow-up-right' },
  { name: 'rayLine',                label: 'Ray',             icon: 'pi pi-chart-line' },
  { name: 'parallelStraightLine',   label: 'Channel',         icon: 'pi pi-bars' },
  { name: 'fibonacciLine',          label: 'Fibonacci',       icon: 'pi pi-percentage' },
  { name: 'brush',                  label: 'Free draw',       icon: 'pi pi-pencil' },
]

// KLineChart built-in indicators; 'main' draws over the candles, 'sub' gets its own pane.
const INDICATORS = [
  { name: 'MA',   label: 'MA',     pane: 'main' },
  { name: 'EMA',  label: 'EMA',    pane: 'main' },
  { name: 'BOLL', label: 'BOLL',   pane: 'main' },
  { name: 'SAR',  label: 'SAR',    pane: 'main' },
  { name: 'VOL',  label: 'Volume', pane: 'sub' },
  { name: 'MACD', label: 'MACD',   pane: 'sub' },
  { name: 'RSI',  label: 'RSI',    pane: 'sub' },
  { name: 'KDJ',  label: 'KDJ',    pane: 'sub' },
]
const DEFAULT_INDICATORS = ['MA', 'VOL']
const CANDLE_PANE = 'candle_pane'

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
  await nextTick()
  mountChart()
}

async function changeTimeframe(tf) {
  if (interval.value === tf) return
  interval.value = tf
  chart?.setPeriod(TIMEFRAMES.find(t => t.value === tf).period)
}

// ── Chart ────────────────────────────────────────────────────────
function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return v || fallback
}

function chartStyles() {
  const text   = cssVar('--jd-text-muted', '#7683a8')
  const line   = cssVar('--jd-line-2', '#131a30')
  const border = cssVar('--jd-border', '#1b2440')
  const up     = cssVar('--jd-green', '#2ee08a')
  const down   = cssVar('--jd-red', '#ff5470')
  const cyan   = cssVar('--jd-cyan', '#3fe0ff')
  const body   = cssVar('--jd-body', '#05070f')
  const mono   = cssVar('--jd-mono', 'monospace')
  const axis = {
    axisLine: { color: border },
    tickLine: { color: border },
    tickText: { color: text, family: mono, size: 11 },
  }
  const crosshairSide = {
    line: { color: cyan },
    text: { color: body, backgroundColor: cyan, family: mono },
  }
  return {
    grid: { horizontal: { color: line }, vertical: { color: line } },
    candle: {
      bar: {
        upColor: up, downColor: down,
        upBorderColor: up, downBorderColor: down,
        upWickColor: up, downWickColor: down,
      },
      priceMark: { high: { color: text }, low: { color: text } },
      tooltip: { text: { color: text, family: mono, size: 11 } },
    },
    indicator: {
      ohlc: { upColor: up, downColor: down },
      tooltip: { text: { color: text, family: mono, size: 11 } },
    },
    xAxis: axis,
    yAxis: axis,
    separator: { color: border },
    crosshair: { horizontal: crosshairSide, vertical: crosshairSide },
    overlay: {
      line: { color: cyan },
      point: { color: cyan, borderColor: cssVar('--jd-blue-glow', 'rgba(63,224,255,0.14)') },
      text: { color: text, family: mono },
    },
  }
}

function mountChart() {
  if (chart || !chartContainer.value) return
  chart = init(chartContainer.value, { locale: 'en-US', styles: chartStyles() })
  if (!chart) return
  restored = false
  chart.setDataLoader({ getBars: loadBars })
  chart.setSymbol({ ticker: props.symbol, pricePrecision: 2, volumePrecision: 0 })
  chart.setPeriod(TIMEFRAMES.find(t => t.value === interval.value).period)
}

function destroyChart() {
  if (!chart) return
  dispose(chartContainer.value)
  chart = null
  pendingOverlayId = null
  activeTool.value = null
}

async function loadBars({ type, callback }) {
  if (type !== 'init') {
    callback([], false)
    return
  }
  loadingCandles.value = true
  try {
    const res = await marketApi.getCandles(props.symbol, { interval: interval.value, limit: 500 })
    const bars = (res.data || []).map(c => ({
      timestamp: c.timestamp,
      open: c.open, high: c.high, low: c.low, close: c.close, volume: c.volume,
    }))
    noData.value = bars.length === 0
    callback(bars, false)
    if (!restored) {
      restored = true
      restoreIndicators()
      restoreDrawings()
    }
  } catch (e) {
    console.error('candle load failed', e)
    noData.value = true
    callback([], false)
  } finally {
    loadingCandles.value = false
  }
}

function onResize() {
  chart?.resize()
}

// ── Indicators (persisted per symbol) ────────────────────────────
function paneIdFor(name) {
  const meta = INDICATORS.find(i => i.name === name)
  return meta?.pane === 'main' ? CANDLE_PANE : `pane_${name}`
}

function restoreIndicators() {
  activeIndicators.value = readStored('indicators', [...DEFAULT_INDICATORS])
    .filter(name => INDICATORS.some(i => i.name === name))
  activeIndicators.value.forEach(name => chart.createIndicator({ name, paneId: paneIdFor(name) }))
}

function toggleIndicator(name) {
  if (!chart) return
  if (activeIndicators.value.includes(name)) {
    chart.removeIndicator({ paneId: paneIdFor(name), name })
    activeIndicators.value = activeIndicators.value.filter(n => n !== name)
  } else {
    chart.createIndicator({ name, paneId: paneIdFor(name) })
    activeIndicators.value = [...activeIndicators.value, name]
  }
  writeStored('indicators', activeIndicators.value)
}

// ── Drawings (persisted per symbol) ──────────────────────────────
function overlayCallbacks() {
  return {
    onDrawEnd: () => {
      pendingOverlayId = null
      activeTool.value = null
      persistDrawings()
      return false
    },
    onPressedMoveEnd: () => { persistDrawings(); return false },
    onRemoved: () => { persistDrawings(); return false },
  }
}

function selectTool(name) {
  if (!chart) return
  cancelPendingDraw()
  if (activeTool.value === name) {
    activeTool.value = null
    return
  }
  activeTool.value = name
  pendingOverlayId = chart.createOverlay({ name, ...overlayCallbacks() })
}

function cancelPendingDraw() {
  if (chart && pendingOverlayId) chart.removeOverlay({ id: pendingOverlayId })
  pendingOverlayId = null
}

function clearDrawings() {
  if (!chart) return
  cancelPendingDraw()
  activeTool.value = null
  chart.removeOverlay()
  writeStored('overlays', [])
}

function persistDrawings() {
  if (!chart) return
  const overlays = chart.getOverlays()
    .map(o => ({
      name: o.name,
      points: (o.points || []).map(pt => ({ timestamp: pt.timestamp, value: pt.value })),
    }))
    .filter(o => o.points.length > 0 && o.points.every(pt => pt.timestamp != null))
  writeStored('overlays', overlays)
}

function restoreDrawings() {
  readStored('overlays', []).forEach(o => {
    chart.createOverlay({ name: o.name, points: o.points, ...overlayCallbacks() })
  })
}

// ── Per-symbol chart preferences (localStorage; DB persistence is out of scope) ──
function storageKey(kind) {
  return `chart_${kind}_${props.symbol}`
}

function readStored(kind, fallback) {
  try {
    const raw = localStorage.getItem(storageKey(kind))
    const parsed = raw ? JSON.parse(raw) : null
    return Array.isArray(parsed) ? parsed : fallback
  } catch {
    return fallback
  }
}

function writeStored(kind, value) {
  try {
    localStorage.setItem(storageKey(kind), JSON.stringify(value))
  } catch { /* private mode / quota — drawings just don't survive a reload */ }
}

function closeIndicatorMenu() {
  indicatorMenuOpen.value = false
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
  document.addEventListener('click', closeIndicatorMenu)
  await loadData()
  await checkWatchlist()
})

onUnmounted(() => {
  window.removeEventListener('resize', onResize)
  document.removeEventListener('click', closeIndicatorMenu)
  destroyChart()
})

watch(() => props.symbol, async () => {
  destroyChart()
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

.chart-controls {
  display: flex;
  align-items: center;
  gap: 12px;
}

.indicator-menu {
  position: relative;
}

.indicator-trigger {
  font-size: 0.75rem;
  padding: 6px 10px;
}

.indicator-dropdown {
  position: absolute;
  right: 0;
  top: calc(100% + 6px);
  z-index: 20;
  min-width: 190px;
  padding: 6px;
  background: var(--jd-card-2);
  border: 1px solid var(--jd-border);
  border-radius: 8px;
  box-shadow: var(--jd-shadow-card);
}

.indicator-option {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 7px 8px;
  background: none;
  border: none;
  border-radius: 6px;
  color: var(--jd-text-muted);
  font-family: var(--jd-mono);
  font-size: 0.75rem;
  cursor: pointer;
  transition: background var(--jd-trans), color var(--jd-trans);
}

.indicator-option:hover {
  background: var(--jd-card-hover);
  color: var(--jd-text);
}

.indicator-option.active {
  color: var(--jd-cyan);
}

.indicator-name {
  flex: 1;
  text-align: left;
}

.indicator-pane {
  color: var(--jd-text-faint);
  font-size: 0.65rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

.chart-body {
  display: flex;
  gap: 10px;
}

.draw-toolbar {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex-shrink: 0;
  padding-right: 10px;
  border-right: 1px solid var(--jd-border);
}

.draw-tool {
  width: 30px;
  height: 30px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: none;
  border: 1px solid transparent;
  border-radius: 6px;
  color: var(--jd-text-muted);
  font-size: 12px;
  cursor: pointer;
  transition: all var(--jd-trans);
}

.draw-tool:hover {
  background: var(--jd-card-hover);
  color: var(--jd-text);
}

.draw-tool.active {
  border-color: var(--jd-cyan);
  color: var(--jd-cyan);
  box-shadow: var(--jd-shadow-glow);
}

.draw-tool.danger:hover {
  color: var(--jd-red);
}

.rot90 {
  transform: rotate(90deg);
}

.chart-wrap {
  position: relative;
  flex: 1;
  min-width: 0;
}

.chart-container {
  width: 100%;
  height: 520px;
}

.chart-msg {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--jd-card);
  color: var(--jd-text-muted);
}

.chart-msg i {
  font-size: 24px;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
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
