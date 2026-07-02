<template>
  <div class="jd-page">
    <!-- Analysis Controls -->
    <div class="jd-card">
      <div class="jd-card-body">
        <div class="grid grid-cols-1 md:grid-cols-4 gap-4">
          <div>
            <label style="display: block; color: var(--jd-text-muted); font-size: 0.875rem; margin-bottom: 8px">Select Symbol</label>
            <input
              v-model="symbol"
              class="jd-input w-full"
              placeholder="e.g. BTC/USDT or AAPL"
              @keyup.enter="onRun"
            />
            <p style="color: var(--jd-text-muted); font-size: 0.72rem; margin-top: 6px">
              Routes to <strong>{{ exchangeType }}</strong> ({{ symbol.includes('/') ? 'crypto pair' : 'equity' }})
            </p>
          </div>
          <div>
            <label style="display: block; color: var(--jd-text-muted); font-size: 0.875rem; margin-bottom: 8px">Timeframe</label>
            <select v-model="timeframe" class="jd-input jd-select w-full">
              <option v-for="o in timeframes" :key="o.value" :value="o.value">{{ o.label }}</option>
            </select>
          </div>
          <div>
            <label style="display: block; color: var(--jd-text-muted); font-size: 0.875rem; margin-bottom: 8px">Model</label>
            <select v-model="model" class="jd-input jd-select w-full">
              <option v-for="o in models" :key="o.value" :value="o.value">{{ o.label }}</option>
            </select>
          </div>
          <div class="flex items-end">
            <button
              class="jd-btn jd-btn-primary w-full"
              :disabled="loading || !symbol.trim()"
              @click="onRun"
            >
              <i class="pi" :class="loading ? 'pi-spinner pi-spin' : 'pi-play'"></i>
              {{ loading ? 'Analyzing…' : 'Run Analysis' }}
            </button>
          </div>
        </div>
        <p style="color: var(--jd-text-muted); font-size: 0.72rem; margin-top: 10px">
          <i class="pi pi-info-circle"></i>
          Timeframe &amp; Model are display-only — the analysis backend derives its own indicators from the symbol.
        </p>
        <div v-if="error" class="jd-badge red" style="margin-top: 12px; display: inline-block">
          <i class="pi pi-exclamation-triangle"></i> {{ error }}
        </div>
      </div>
    </div>

    <!-- Recent Analyses -->
    <div v-if="recent.length" class="jd-card">
      <div class="jd-card-header">
        <h3 class="jd-card-title">Recent Analyses</h3>
      </div>
      <div class="jd-card-body">
        <div style="display: flex; flex-wrap: wrap; gap: 8px">
          <button
            v-for="a in recent"
            :key="a.id"
            class="jd-badge"
            :class="[verdictClass(a.verdict), { 'is-active': result && result.id === a.id }]"
            style="cursor: pointer"
            @click="selectAnalysis(a)"
          >
            <i class="pi pi-chart-line"></i>
            {{ a.symbol }} · {{ (a.action || '—').toUpperCase() }}
            <template v-if="a.confidence != null"> · {{ a.confidence }}%</template>
          </button>
        </div>
      </div>
    </div>

    <!-- Analysis Results Grid -->
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
      <!-- Sentiment Analysis -->
      <div class="jd-card">
        <div class="jd-card-header">
          <h3 class="jd-card-title">Sentiment Analysis</h3>
        </div>
        <div class="jd-card-body">
          <div style="display: flex; flex-direction: column; gap: 16px">
            <div style="text-align: center">
              <p style="font-size: 3rem; font-weight: bold; color: var(--jd-blue)">
                {{ result && result.confidence != null ? result.confidence + '%' : '--' }}
              </p>
              <p style="color: var(--jd-text-muted); font-size: 0.875rem; margin-top: 8px">Confidence</p>
            </div>
            <div style="background: rgba(0, 0, 0, 0.2); padding: 12px; border-radius: 4px; text-align: center">
              <template v-if="result">
                <span class="jd-badge" :class="verdictClass(result.verdict)">
                  {{ (result.verdict || 'n/a').toUpperCase() }}
                </span>
                <span v-if="result.sentiment" class="jd-badge gray" style="margin-left: 6px">
                  {{ result.sentiment }}
                </span>
              </template>
              <p v-else style="color: var(--jd-text-muted); font-size: 0.875rem">No analysis available</p>
            </div>
          </div>
        </div>
      </div>

      <!-- Price Prediction -->
      <div class="jd-card">
        <div class="jd-card-header">
          <h3 class="jd-card-title">Price Prediction</h3>
        </div>
        <div class="jd-card-body">
          <div style="display: flex; flex-direction: column; gap: 12px">
            <div style="display: flex; justify-content: space-between">
              <span style="color: var(--jd-text-muted); font-size: 0.875rem">Entry</span>
              <span style="font-weight: 600; color: var(--jd-text)">{{ price(result && result.entry_price) }}</span>
            </div>
            <div style="display: flex; justify-content: space-between">
              <span style="color: var(--jd-text-muted); font-size: 0.875rem">Stop Loss</span>
              <span style="font-weight: 600; color: var(--jd-red)">{{ price(result && result.stop_loss) }}</span>
            </div>
            <div style="display: flex; justify-content: space-between">
              <span style="color: var(--jd-text-muted); font-size: 0.875rem">Take Profit</span>
              <span style="font-weight: 600; color: var(--jd-green)">{{ price(result && result.take_profit) }}</span>
            </div>
            <div v-if="result && result.risk_reward_ratio != null" style="display: flex; justify-content: space-between">
              <span style="color: var(--jd-text-muted); font-size: 0.875rem">Risk / Reward</span>
              <span style="font-weight: 600; color: var(--jd-text)">{{ num(result.risk_reward_ratio) }}</span>
            </div>
            <div>
              <p style="color: var(--jd-text-muted); font-size: 0.875rem; margin-bottom: 4px">Confidence</p>
              <div class="pbar mb-2"><i :style="{ width: (result && result.confidence != null ? result.confidence : 0) + '%' }"></i></div>
            </div>
          </div>
        </div>
      </div>

      <!-- Model Performance -->
      <div class="jd-card">
        <div class="jd-card-header">
          <h3 class="jd-card-title">Model Performance</h3>
        </div>
        <div class="jd-card-body">
          <div style="display: flex; flex-direction: column; gap: 12px">
            <div>
              <p style="color: var(--jd-text-muted); font-size: 0.875rem">Confidence</p>
              <p style="font-size: 1.25rem; font-weight: bold; color: var(--jd-text)">
                {{ result && result.confidence != null ? result.confidence + ' %' : '-- %' }}
              </p>
            </div>
            <div>
              <p style="color: var(--jd-text-muted); font-size: 0.875rem">Action</p>
              <span v-if="result && result.action" class="jd-badge" :class="actionClass(result.action)">
                {{ result.action.toUpperCase() }}
              </span>
              <p v-else style="font-size: 1.25rem; font-weight: bold; color: var(--jd-text)">--</p>
            </div>
            <div style="display: flex; justify-content: space-between">
              <span style="color: var(--jd-text-muted); font-size: 0.875rem">Tokens Used</span>
              <span style="font-weight: 600; color: var(--jd-text)">{{ result && result.tokens_used != null ? num(result.tokens_used) : '—' }}</span>
            </div>
            <div style="display: flex; justify-content: space-between">
              <span style="color: var(--jd-text-muted); font-size: 0.875rem">API Cost</span>
              <span style="font-weight: 600; color: var(--jd-text)">{{ result && result.api_cost != null ? '$' + num(result.api_cost, 4) : '—' }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Detailed Report -->
    <div class="jd-card">
      <div class="jd-card-header">
        <h2 class="jd-card-title">Analysis Report</h2>
      </div>
      <div class="jd-card-body">
        <template v-if="result">
          <div style="display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 12px">
            <span class="jd-badge" :class="verdictClass(result.verdict)">{{ (result.verdict || 'n/a').toUpperCase() }}</span>
            <span v-if="result.asset_class" class="jd-badge gray">{{ result.asset_class }}</span>
            <span v-if="result.analysis_type" class="jd-badge cyan">{{ result.analysis_type }}</span>
            <span v-if="result.ai_invoked === false" class="jd-badge yellow" :title="result.ai_skip_reason || ''">AI skipped</span>
          </div>
          <p style="color: var(--jd-text); line-height: 1.6">
            {{ reportText }}
          </p>
          <div v-if="keyFactors.length" style="margin-top: 16px">
            <p style="color: var(--jd-text-muted); font-size: 0.875rem; margin-bottom: 8px">Key Factors</p>
            <div style="display: flex; flex-wrap: wrap; gap: 6px">
              <span v-for="(f, i) in keyFactors" :key="i" class="jd-chip">{{ f }}</span>
            </div>
          </div>
          <div
            v-if="result.claude_response && result.claude_response.risk_warning"
            class="jd-badge red"
            style="margin-top: 16px; display: block; white-space: normal; text-align: left; line-height: 1.5"
          >
            <i class="pi pi-exclamation-triangle"></i> {{ result.claude_response.risk_warning }}
          </div>
        </template>
        <div v-else style="text-align: center; padding: 48px 16px">
          <p style="color: var(--jd-text-muted)">Run an analysis to view detailed results and insights</p>
        </div>
      </div>
    </div>

    <!-- Key Metrics -->
    <div class="jd-card">
      <div class="jd-card-header">
        <h2 class="jd-card-title">Key Metrics</h2>
      </div>
      <div class="jd-card-body">
        <DataTable
          :columns="metricColumns"
          :data="metrics"
          :row-key="(r) => r.metric"
          :pagination="false"
          empty-text="No metrics available"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import DataTable from '@/components/common/DataTable.vue'
import { runAnalysis, listAnalyses } from '@/api/analysis'

const timeframes = ref([
  { label: '1H', value: '1h' },
  { label: '4H', value: '4h' },
  { label: '1D', value: '1d' },
  { label: '1W', value: '1w' }
])

const models = ref([
  { label: 'LSTM', value: 'lstm' },
  { label: 'GRU', value: 'gru' },
  { label: 'Transformer', value: 'transformer' }
])

const metricColumns = [
  { key: 'metric', header: 'Metric' },
  { key: 'value', header: 'Value', align: 'right' },
]

// --- state ---
const symbol = ref('')
const timeframe = ref('1d')   // display-only (backend ignores)
const model = ref('lstm')     // display-only (backend ignores)
const loading = ref(false)
const error = ref('')
const result = ref(null)
const recent = ref([])

// exchange_type: crypto pairs contain '/', everything else is an equity on Alpaca
const exchangeType = computed(() => (symbol.value.includes('/') ? 'binance' : 'alpaca'))

// --- formatting helpers (defensive: fields may be null) ---
function num(v, digits = 2) {
  if (v == null || v === '' || Number.isNaN(Number(v))) return '—'
  const n = Number(v)
  return Number.isInteger(n) && digits === 2 ? n.toLocaleString() : n.toLocaleString(undefined, { maximumFractionDigits: digits })
}
function price(v) {
  return v == null ? '—' : '$' + num(v)
}
function verdictClass(v) {
  const k = String(v || '').toLowerCase()
  if (k === 'go') return 'green'
  if (k === 'no-go' || k === 'no_go' || k === 'nogo') return 'red'
  if (k === 'watch') return 'yellow'
  return 'gray'
}
function actionClass(a) {
  const k = String(a || '').toLowerCase()
  if (k === 'buy') return 'green'
  if (k === 'sell') return 'red'
  return 'gray'
}

// --- derived report bits ---
const reportText = computed(() => {
  const r = result.value
  if (!r) return ''
  return (r.claude_response && r.claude_response.reason) || r.verdict_reason || 'No narrative provided for this analysis.'
})
const keyFactors = computed(() => {
  const kf = result.value && result.value.claude_response && result.value.claude_response.key_factors
  return Array.isArray(kf) ? kf : []
})
const metrics = computed(() => {
  const snap = result.value && result.value.indicators_snapshot
  if (!snap || typeof snap !== 'object') return []
  return Object.entries(snap).map(([metric, value]) => ({
    metric,
    value: typeof value === 'number' ? num(value, 4) : (value == null ? '—' : String(value)),
  }))
})

// --- actions ---
async function onRun() {
  if (!symbol.value.trim() || loading.value) return
  loading.value = true
  error.value = ''
  try {
    const res = await runAnalysis({ symbol: symbol.value.trim(), exchange_type: exchangeType.value })
    result.value = res.data
    // put the fresh result at the top of the recent list
    recent.value = [res.data, ...recent.value.filter((a) => a.id !== res.data.id)].slice(0, 10)
  } catch (err) {
    error.value = err.response?.data?.detail || err.message || 'Analysis failed'
  } finally {
    loading.value = false
  }
}

function selectAnalysis(a) {
  result.value = a
  if (a.symbol) symbol.value = a.symbol
}

onMounted(async () => {
  try {
    const res = await listAnalyses(10)
    recent.value = Array.isArray(res.data) ? res.data : []
    // pre-load the newest so the page isn't empty
    if (recent.value.length) selectAnalysis(recent.value[0])
  } catch (err) {
    // non-fatal: leave the page in its empty state
    error.value = err.response?.data?.detail || 'Could not load recent analyses'
  }
})
</script>

<style scoped>
.pbar {
  height: 6px;
  background: var(--jd-border);
  border-radius: 3px;
  overflow: hidden;
}
.pbar i {
  display: block;
  height: 100%;
  background: var(--jd-cyan);
  border-radius: 3px;
  transition: width 0.3s ease;
}
.jd-badge.is-active {
  outline: 1px solid var(--jd-cyan);
  outline-offset: 1px;
}
</style>
