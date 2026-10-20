<template>
  <div class="jd-page">
    <div class="jd-page-head" style="margin-bottom:0">
      <div>
        <h1 class="jd-h1">
          Backtest <span v-if="run" class="jd-mono" style="font-size:0.7em">{{ run.kind }}</span>
          <span v-if="run" :class="['jd-badge', statusColor(run.status)]" style="vertical-align:middle">{{ run.status }}</span>
        </h1>
        <p v-if="run" class="jd-sub">
          <router-link :to="`/groups/${run.group_id}`">{{ run.config_snapshot?.group?.name || run.group_id }}</router-link>
          · {{ run.data_start }}..{{ run.data_end }}
          · created {{ fmtTime(run.created_at) }} · started {{ fmtTime(run.started_at) }} · finished {{ fmtTime(run.finished_at) }}
        </p>
      </div>
      <div class="head-actions">
        <router-link v-if="run?.status === 'succeeded'" :to="`/compare?group_ids=${run.group_id}&run_ids=${run.id}`"
                     class="jd-btn jd-btn-ghost jd-btn-sm">
          <i class="pi pi-chart-line"></i> Compare
        </router-link>
        <button v-if="canCancel" type="button" class="jd-btn jd-btn-danger jd-btn-sm" @click="askCancel">
          <i class="pi pi-times"></i> Cancel
        </button>
        <button type="button" class="jd-btn jd-btn-ghost jd-btn-sm" :disabled="loading" @click="load">
          <i class="pi pi-refresh"></i> Refresh
        </button>
      </div>
    </div>

    <div v-if="error" class="jd-decision nogo" style="padding:24px 22px" role="alert">
      <div class="jd-take" style="margin-bottom:6px"><b>{{ error }}</b></div>
      <button type="button" class="jd-btn jd-btn-ghost jd-btn-sm" @click="load">Try again</button>
    </div>
    <div v-else-if="loading && !run" class="jd-empty"><p>Loading…</p></div>

    <template v-if="run">
      <div class="badges">
        <span v-if="run.survivorship_biased" class="jd-badge orange">survivorship-biased</span>
        <span v-else class="jd-badge green">point-in-time</span>
        <span class="jd-mono" style="font-size:12px">{{ run.universe_label }}</span>
        <span v-if="universe" class="jd-src">
          symbols {{ universe.symbols ?? '—' }}
          <template v-if="universe.sectors_known_pct != null"> · sectors known {{ pct(universe.sectors_known_pct) }}</template>
        </span>
      </div>

      <div v-if="run.status === 'queued' || run.status === 'running'" class="jd-alert info">
        <i class="pi pi-spin pi-spinner"></i>
        <div>
          <div>{{ run.progress?.message || run.status }}</div>
          <div class="jd-src" style="margin-top:3px">heartbeat {{ ageOf(run.progress?.heartbeat_at) }}</div>
          <div v-if="run.cancel_requested" style="margin-top:4px">cancel requested — the worker stops at its next check</div>
        </div>
      </div>

      <div v-if="run.status === 'failed'" class="jd-alert error" role="alert">
        <i class="pi pi-exclamation-triangle"></i> {{ run.error || 'The run failed.' }}
      </div>

      <section v-if="run.status === 'succeeded' && run.metrics_json" class="jd-card">
        <div class="jd-card-header"><h2 class="jd-card-title">Metrics</h2></div>
        <div class="jd-card-body metric-blocks">
          <div v-for="b in metricBlocks" :key="b.title">
            <div class="jd-h2">{{ b.title }}</div>
            <div class="jd-metrics">
              <div v-for="m in METRICS" :key="m.key" class="jd-metric">
                <div class="k">{{ m.label }}</div><div class="v">{{ m.fmt(b.data?.[m.key]) }}</div>
              </div>
            </div>
          </div>
          <div v-if="isWf && run.metrics_json.gate">
            <div class="jd-h2">gate</div>
            <div class="jd-metrics">
              <div class="jd-metric"><div class="k">windows positive</div><div class="v">{{ pct(run.metrics_json.gate.windows_positive_pct) }}</div></div>
              <div class="jd-metric"><div class="k">window PF median</div><div class="v">{{ num(run.metrics_json.gate.window_pf_median) }}</div></div>
            </div>
          </div>
        </div>
      </section>

      <section v-if="isWf && run.metrics_json?.windows?.length" class="jd-card">
        <div class="jd-card-header"><h2 class="jd-card-title">Walk-forward windows</h2></div>
        <div class="jd-card-body">
          <div class="jd-table-wrap">
            <table class="jd-table">
              <thead>
                <tr>
                  <th>Window</th><th>IS</th><th>OOS</th><th>Params</th>
                  <th class="th-right">Fixed PF</th><th class="th-right">Fixed Sharpe</th><th class="th-right">Fixed n</th>
                  <th class="th-right">Sel PF</th><th class="th-right">Sel Sharpe</th><th class="th-right">Sel n</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="w in run.metrics_json.windows" :key="w.window">
                  <td>{{ w.window }}</td><td>{{ w.is }}</td><td>{{ w.oos }}</td>
                  <td style="font-size:11px">{{ paramsText(w.params) }}</td>
                  <td class="td-right">{{ num(w.fixed?.profit_factor) }}</td>
                  <td class="td-right">{{ num(w.fixed?.sharpe) }}</td>
                  <td class="td-right">{{ w.fixed?.num_trades ?? '—' }}</td>
                  <td class="td-right">{{ num(w.selected?.profit_factor) }}</td>
                  <td class="td-right">{{ num(w.selected?.sharpe) }}</td>
                  <td class="td-right">{{ w.selected?.num_trades ?? '—' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section v-if="isWf && cal" class="jd-card">
        <div class="jd-card-header flex-header">
          <h2 class="jd-card-title">Calibration</h2>
          <span :class="['jd-badge', cal.status === 'ok' ? 'green' : 'yellow']">{{ cal.status }}</span>
        </div>
        <div class="jd-card-body">
          <p class="jd-src cal-text">
            n {{ cal.n }} · base rate {{ num(cal.base_rate, 4) }} · brier in-sample {{ num(cal.brier_in_sample, 4) }}
            · LOO window brier {{ num(cal.loo_window_brier, 4) }} vs base-rate brier {{ num(cal.loo_base_rate_brier, 4) }}
            · skipped (no score) {{ cal.skipped_no_score }}
          </p>
          <svg v-if="calBars.length" viewBox="0 0 600 180" class="cal-svg">
            <rect v-for="b in calBars" :key="b.key" :x="b.x" :y="b.y" :width="b.w" :height="b.h" class="bar" />
            <text v-for="b in calBars" :key="`t${b.key}`" :x="b.x + b.w / 2" :y="b.y - 4" text-anchor="middle" class="label">
              p {{ b.p.toFixed(3) }} · n {{ b.n }}
            </text>
            <text v-for="b in calBars" :key="`l${b.key}`" :x="b.x + b.w / 2" y="176" text-anchor="middle" class="label">
              {{ b.lo.toFixed(0) }}–{{ b.hi.toFixed(0) }}
            </text>
            <line v-if="cal.base_rate != null" x1="0" x2="600" :y1="calY(cal.base_rate)" :y2="calY(cal.base_rate)" class="base" />
            <line x1="0" x2="600" :y1="calY(0)" :y2="calY(0)" class="axis" />
          </svg>
        </div>
      </section>

      <section class="jd-card">
        <div class="jd-card-header"><h2 class="jd-card-title">Equity</h2></div>
        <div class="jd-card-body">
          <p v-if="run.status !== 'succeeded'" class="d-nodata">Equity appears after the run succeeds.</p>
          <div v-else-if="equityLoading" class="jd-empty"><span class="judia-spinner" style="width:26px;height:26px"></span></div>
          <div v-else-if="equityError" class="jd-alert error" role="alert">
            <i class="pi pi-exclamation-triangle"></i> {{ equityError }}
          </div>
          <EquityOverlay v-else :series="equitySeries" />
        </div>
      </section>
    </template>

    <Modal v-model="showCancel" title="Cancel backtest" width="480px">
      <p class="confirm-text">
        {{ run?.status === 'running' ? 'Ask the worker to stop this run at its next check?' : 'Cancel this queued run?' }}
      </p>
      <div v-if="cancelError" class="jd-alert error" role="alert" style="margin-top:12px">
        <i class="pi pi-exclamation-triangle"></i> {{ cancelError }}
      </div>
      <template #footer>
        <button type="button" class="jd-btn jd-btn-ghost jd-btn-sm" @click="showCancel = false">Close</button>
        <button type="button" class="jd-btn jd-btn-danger jd-btn-sm" :disabled="cancelling" @click="doCancel">
          <i v-if="cancelling" class="pi pi-spin pi-spinner"></i> Cancel run
        </button>
      </template>
    </Modal>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import EquityOverlay from '@/components/charts/EquityOverlay.vue'
import Modal from '@/components/common/Modal.vue'
import { getBacktest, getBacktestEquity, cancelBacktest } from '@/api/backtests'
import { useAuthStore } from '@/stores/auth'
import { detailOf } from '@/composables/useApiError'

const props = defineProps({
  id: { type: String, required: true },
})

const auth = useAuthStore()

const run = ref(null)
const loading = ref(false)
const error = ref(null)
const equitySeries = ref([])
const equityLoading = ref(false)
const equityError = ref(null)
const showCancel = ref(false)
const cancelling = ref(false)
const cancelError = ref(null)

const num = (v, digits = 2) => (v == null ? '—' : Number(v).toFixed(digits))
const pct = (v) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`)
const METRICS = [
  { key: 'profit_factor', label: 'PF', fmt: (v) => num(v) },
  { key: 'sharpe', label: 'Sharpe', fmt: (v) => num(v) },
  { key: 'max_drawdown', label: 'max DD', fmt: pct },
  { key: 'cagr', label: 'CAGR', fmt: pct },
  { key: 'win_rate', label: 'win', fmt: pct },
  { key: 'num_trades', label: 'trades', fmt: (v) => (v == null ? '—' : v) },
  { key: 'turnover', label: 'turnover', fmt: (v) => num(v) },
  { key: 'final_equity', label: 'final equity', fmt: (v) => (v == null ? '—' : `$${Number(v).toFixed(2)}`) },
]

const statusColor = (s) => ({ queued: 'gray', running: 'blue', succeeded: 'green', failed: 'red', cancelled: 'gray' }[s] || 'gray')
const isWf = computed(() => run.value?.kind === 'walk_forward')
const universe = computed(() => run.value?.metrics_json?.universe || null)
const cal = computed(() => run.value?.calibration_json || null)
const canCancel = computed(() => auth.isAdmin && run.value
  && (run.value.status === 'queued' || run.value.status === 'running') && !run.value.cancel_requested)

const metricBlocks = computed(() => {
  const m = run.value?.metrics_json || {}
  return isWf.value
    ? [{ title: 'fixed', data: m.fixed }, { title: 'selected', data: m.selected }]
    : [{ title: 'summary', data: m.summary }]
})

const paramsText = (params) => Object.entries(params || {}).map(([k, v]) => `${k}=${v}`).join(' · ')

const fmtTime = (iso) => {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

const ageOf = (iso) => {
  if (!iso) return 'never'
  const mins = Math.floor((Date.now() - new Date(iso).getTime()) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  if (mins < 48 * 60) return `${Math.floor(mins / 60)}h ago`
  return `${Math.floor(mins / 1440)}d ago`
}

const CAL_TOP = 20
const CAL_BOTTOM = 162
const calY = (p) => CAL_BOTTOM - p * (CAL_BOTTOM - CAL_TOP)
const calBars = computed(() => {
  const blocks = cal.value?.blocks || []
  const slot = 600 / Math.max(blocks.length, 1)
  return blocks.map((b, i) => ({
    key: i, p: b.p, n: b.n, lo: b.lo, hi: b.hi,
    x: i * slot + slot * 0.15, w: slot * 0.7, y: calY(b.p), h: CAL_BOTTOM - calY(b.p),
  }))
})

async function loadEquity(id, kind) {
  equityLoading.value = true
  equityError.value = null
  try {
    const { data } = await getBacktestEquity(id)
    if (id !== props.id) return
    equitySeries.value = kind === 'walk_forward'
      ? [{ id: 'fixed', label: 'fixed OOS', ...data.fixed }, { id: 'selected', label: 'selected OOS', ...data.selected }]
      : [{ id: 'equity', label: 'equity', ...data.equity }]
  } catch (e) {
    if (id === props.id) equityError.value = detailOf(e, 'Equity unavailable')
  } finally {
    if (id === props.id) equityLoading.value = false
  }
}

async function load() {
  const id = props.id
  loading.value = true
  error.value = null
  try {
    const { data } = await getBacktest(id)
    if (id !== props.id) return
    run.value = data
    if (data.status === 'succeeded') loadEquity(id, data.kind)
  } catch (e) {
    if (id === props.id) error.value = detailOf(e, 'Failed to load the backtest')
  } finally {
    if (id === props.id) loading.value = false
  }
}

function askCancel() {
  cancelError.value = null
  showCancel.value = true
}

async function doCancel() {
  if (cancelling.value) return
  cancelling.value = true
  cancelError.value = null
  try {
    await cancelBacktest(props.id)
    showCancel.value = false
    await load()
  } catch (e) {
    cancelError.value = detailOf(e, 'Cancel failed')
  } finally {
    cancelling.value = false
  }
}

onMounted(load)
watch(() => props.id, () => {
  run.value = null
  equitySeries.value = []
  equityError.value = null
  load()
})
</script>

<style scoped>
.head-actions { display: flex; align-items: flex-end; gap: 10px; flex-wrap: wrap; justify-content: flex-end; }
.jd-sub a { color: var(--jd-cyan); }
.badges { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.metric-blocks { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 18px; }
.flex-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.cal-text { margin: 0 0 10px; }
.cal-svg { display: block; width: 100%; max-width: 720px; height: auto; }
.cal-svg .bar { fill: rgba(63,224,255,0.25); stroke: var(--jd-cyan); stroke-width: 1; }
.cal-svg .label { font-family: var(--jd-mono); font-size: 10px; fill: var(--jd-text-muted); }
.cal-svg .base { stroke: var(--jd-yellow); stroke-width: 1; stroke-dasharray: 4 4; }
.cal-svg .axis { stroke: var(--jd-line-2); stroke-width: 1; }
.confirm-text { margin: 0; font-size: 13.5px; line-height: 1.5; color: var(--jd-text); }
</style>
