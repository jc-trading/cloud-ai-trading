<template>
  <div class="scope">
    <!-- Filter rail + refresh -->
    <div class="scope-rail">
      <div class="segmented" role="group" aria-label="Filter decisions by asset class">
        <button
          v-for="opt in assetFilters"
          :key="opt.value"
          type="button"
          class="seg"
          :class="{ active: assetClass === opt.value }"
          :aria-pressed="assetClass === opt.value"
          @click="setAssetClass(opt.value)"
        >{{ opt.label }}</button>
      </div>
      <div class="rail-meta">
        <span v-if="lastUpdated"><span class="jd-live-dot" :class="{ red: !!error }"></span> sweep {{ lastUpdatedLabel }}</span>
        <button type="button" class="jd-btn jd-btn-ghost jd-btn-sm" :disabled="loading" @click="loadDecisions">
          <i class="pi pi-refresh" :class="{ 'animate-spin': loading }"></i>{{ loading ? 'Reading' : 'Refresh' }}
        </button>
      </div>
    </div>

    <!-- Data-first stat strip -->
    <div class="jd-strip" v-if="decisions.length || !loading">
      <div class="jd-strip-item"><div class="l">Watched</div><div class="v">{{ stats.total }} <small>symbols</small></div></div>
      <div class="jd-strip-item"><div class="l">Go / Watch / No-Go</div><div class="v"><span style="color:var(--jd-green)">{{ stats.go }}</span> · <span style="color:var(--jd-yellow)">{{ stats.watch }}</span> · <span style="color:var(--jd-red)">{{ stats.nogo }}</span></div></div>
      <div class="jd-strip-item"><div class="l">Avg confidence</div><div class="v" style="color:var(--jd-cyan)">{{ stats.avg }}<small>%</small></div></div>
      <div class="jd-strip-item"><div class="l">AI invoked</div><div class="v">{{ stats.aiCount }} <small>/ {{ stats.total }}</small></div></div>
    </div>

    <!-- Error -->
    <div v-if="error" class="jd-decision nogo" style="padding:24px 22px" role="alert">
      <div class="jd-take" style="margin-bottom:6px"><b>Could not read the decision log.</b></div>
      <p style="color:var(--jd-text-muted);font-size:13px;margin-bottom:14px">{{ error }}</p>
      <button type="button" class="jd-btn jd-btn-ghost jd-btn-sm" @click="loadDecisions">Try again</button>
    </div>

    <!-- Loading skeleton -->
    <div v-else-if="loading && !decisions.length" class="scope-grid" aria-hidden="true">
      <div v-for="n in 4" :key="n" class="jd-decision skeleton">
        <div class="sk sk-lg"></div><div class="sk sk-md"></div><div class="sk sk-block"></div>
      </div>
    </div>

    <!-- Empty -->
    <div v-else-if="!decisions.length" class="jd-empty">
      <i class="pi pi-inbox"></i>
      <p>No verdicts on file yet.</p>
      <p style="font-size:13px;max-width:420px">The analyst records a decision each cycle. Add symbols to your watchlist or trigger an analysis, and their verdicts land here.</p>
      <router-link to="/watchlist" class="jd-btn jd-btn-ghost jd-btn-sm">Open watchlist<i class="pi pi-arrow-right"></i></router-link>
    </div>

    <!-- The feed: one instrument card per symbol -->
    <div v-else class="scope-grid">
      <article
        v-for="d in decisions"
        :key="d.id"
        class="jd-decision"
        :class="verdictKey(d.verdict)"
        tabindex="0"
        @click="openCaseFile(d.symbol)"
        @keydown.enter="openCaseFile(d.symbol)"
      >
        <!-- Header -->
        <div class="d-head">
          <span class="d-sym">{{ d.symbol }}</span>
          <span class="jd-badge" :class="assetKey(d.asset_class) === 'equity' ? 'purple' : 'cyan'">{{ d.asset_class }}</span>
          <span v-if="d.position_id" class="jd-badge green" title="Order placed (paper) — position open">● position</span>
          <svg class="d-headspark" viewBox="0 0 88 22" preserveAspectRatio="none">
            <polyline :points="wavePoints(d.symbol + '-h', verdictDir(d.verdict), 88, 22, 6)" fill="none" :stroke="verdictColor(d.verdict)" stroke-width="1.5" />
          </svg>
          <span class="jd-verdict" :class="verdictKey(d.verdict)">{{ verdictLabel(d.verdict) }}</span>
        </div>

        <!-- Gauge + metrics -->
        <div class="d-body">
          <div class="jd-gauge" :class="verdictKey(d.verdict)">
            <svg width="116" height="116" viewBox="0 0 116 116">
              <circle class="ring-bg" cx="58" cy="58" r="50" fill="none" stroke-width="8" />
              <circle class="ring-fg" cx="58" cy="58" r="50" fill="none" stroke-width="8"
                :stroke-dasharray="GAUGE_C" :stroke-dashoffset="gaugeOffset(d.confidence)" />
            </svg>
            <div class="lab">
              <div class="n">{{ d.confidence != null ? clampPct(d.confidence) : '—' }}<small v-if="d.confidence != null">%</small></div>
              <div class="t">{{ String(d.action || '').toUpperCase() || 'hold' }}</div>
            </div>
          </div>
          <div class="d-right">
            <div class="jd-metrics" v-if="dataEntries(d).length">
              <div class="jd-metric" v-for="row in dataEntries(d)" :key="row.k">
                <div class="k">{{ row.k }}</div><div class="v">{{ row.v }}</div>
              </div>
            </div>
            <p v-else class="d-nodata">No indicator snapshot on this cycle.</p>
          </div>
        </div>

        <!-- Footer: waveform + concise reasoning -->
        <div class="d-foot">
          <div class="jd-wave">
            <svg viewBox="0 0 300 38" preserveAspectRatio="none">
              <polyline :points="wavePoints(d.symbol, verdictDir(d.verdict))" fill="none" :stroke="verdictColor(d.verdict)" stroke-width="1.5" :style="{ filter: `drop-shadow(0 0 4px ${verdictColor(d.verdict)})` }" />
            </svg>
          </div>
          <div class="d-reason">
            <span v-for="(c, i) in chips(d)" :key="i" class="jd-chip">{{ c }}</span>
            <span v-if="shortTake(d)" class="jd-take">{{ shortTake(d) }}</span>
            <span class="jd-src">{{ d.ai_invoked ? 'ai' : (skipReason(d) || 'no ai') }}</span>
          </div>
        </div>
      </article>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { getDecisions } from '@/api/decisions'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'

dayjs.extend(relativeTime)

const router = useRouter()

const decisions = ref([])
const loading = ref(false)
const error = ref(null)
const assetClass = ref('') // '' = all, 'crypto', 'equity'
const lastUpdated = ref(null)
let poll = null
let clock = null
const now = ref(Date.now())

const assetFilters = [
  { label: 'All', value: '' },
  { label: 'Crypto', value: 'crypto' },
  { label: 'Equity', value: 'equity' },
]

const MAX_METRICS = 6
const GAUGE_C = (2 * Math.PI * 50).toFixed(2) // ring circumference

const loadDecisions = async () => {
  loading.value = true
  error.value = null
  try {
    const res = await getDecisions(assetClass.value || null)
    decisions.value = Array.isArray(res.data) ? res.data : []
    lastUpdated.value = Date.now()
  } catch (err) {
    error.value = err.response?.data?.detail || 'The decisions endpoint did not respond. Check that the API is up.'
    console.error('Failed to load decisions:', err)
  } finally {
    loading.value = false
  }
}

const setAssetClass = (value) => {
  if (assetClass.value === value) return
  assetClass.value = value
  loadDecisions()
}

const openCaseFile = (symbol) => router.push({ name: 'SymbolDetail', params: { symbol } })

// --- Verdict / action / asset helpers ---
const verdictKey = (v) => {
  const s = String(v || '').toLowerCase()
  if (s === 'go') return 'go'
  if (s === 'no-go' || s === 'no_go' || s === 'nogo') return 'nogo'
  return 'watch'
}
const verdictLabel = (v) => {
  const k = verdictKey(v)
  return k === 'nogo' ? 'NO-GO' : k.toUpperCase()
}
const VERDICT_COLORS = { go: '#2ee08a', watch: '#ffc24b', nogo: '#ff5470' }
const verdictColor = (v) => VERDICT_COLORS[verdictKey(v)]
const verdictDir = (v) => ({ go: 1, watch: 0, nogo: -1 }[verdictKey(v)])
const assetKey = (c) => (String(c || '').toLowerCase() === 'equity' ? 'equity' : 'crypto')
const clampPct = (n) => Math.max(0, Math.min(100, Math.round(Number(n) || 0)))

// gauge: offset so the arc fills clockwise to pct
const gaugeOffset = (n) => (Number(GAUGE_C) * (1 - clampPct(n) / 100)).toFixed(2)

// --- Ambient waveform (deterministic per symbol; visual signal motif, not a live series) ---
const hashSeed = (s) => { let h = 0; for (const c of String(s)) h = (h * 31 + c.charCodeAt(0)) >>> 0; return h || 1 }
const wavePoints = (seed, dir = 0, w = 300, h = 38, n = 11) => {
  let x = (hashSeed(seed) % 2147483646) + 1
  const rnd = () => (x = (x * 16807) % 2147483647) / 2147483647
  const mid = h / 2
  const pts = []
  for (let i = 0; i < n; i++) {
    const t = i / (n - 1)
    const trend = -dir * (t - 0.5) * (h * 0.5)
    const noise = (rnd() - 0.5) * (h * 0.4)
    let y = mid + trend + noise
    y = Math.max(4, Math.min(h - 4, y))
    pts.push(`${(t * w).toFixed(0)},${y.toFixed(1)}`)
  }
  return pts.join(' ')
}

// --- Pulled-data metrics (indicators_snapshot / FA) ---
const formatVal = (v) => {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'number') {
    if (!Number.isFinite(v)) return '—'
    const abs = Math.abs(v)
    if (abs !== 0 && (abs < 0.001 || abs >= 1e7)) return v.toExponential(2)
    return Number.isInteger(v) ? String(v) : v.toFixed(abs >= 100 ? 2 : 4)
  }
  if (typeof v === 'boolean') return v ? 'yes' : 'no'
  if (typeof v === 'object') {
    const s = JSON.stringify(v)
    return s.length > 20 ? s.slice(0, 19) + '…' : s
  }
  const s = String(v)
  return s.length > 18 ? s.slice(0, 17) + '…' : s
}
const snapshotEntries = (d) => {
  const snap = d?.indicators_snapshot
  if (!snap || typeof snap !== 'object') return []
  return Object.entries(snap).filter(([, v]) => v !== null && v !== undefined && v !== '')
}
const prettyKey = (k) => String(k).replace(/_/g, ' ').replace(/\b(eps|rsi|macd|bb|pct|fa|ai)\b/gi, (m) => m.toUpperCase())
const dataEntries = (d) => snapshotEntries(d).slice(0, MAX_METRICS).map(([k, v]) => ({ k: prettyKey(k), v: formatVal(v) }))

// --- Concise reasoning: chips + one short take (data-viz > prose) ---
const reasoning = (d) => {
  if (d?.verdict_reason) return d.verdict_reason
  const r = d?.claude_response?.reason
  return typeof r === 'string' ? r : ''
}
const chips = (d) => {
  const f = d?.claude_response?.key_factors
  const arr = Array.isArray(f) ? f.filter((x) => typeof x === 'string' && x.trim()) : []
  return arr.slice(0, 3).map((s) => (s.length > 26 ? s.slice(0, 25) + '…' : s))
}
const shortTake = (d) => {
  const r = reasoning(d)
  if (!r) return ''
  const first = r.split(/(?<=[.!?])\s/)[0] || r
  return first.length > 96 ? first.slice(0, 95) + '…' : first
}
const skipReason = (d) => {
  const s = d?.ai_skip_reason
  return typeof s === 'string' && s.trim() ? s.replace(/_/g, ' ') : ''
}

// --- Stats strip ---
const stats = computed(() => {
  const arr = decisions.value
  const cnt = (k) => arr.filter((d) => verdictKey(d.verdict) === k).length
  const confs = arr.map((d) => clampPct(d.confidence)).filter((n) => n > 0)
  const avg = confs.length ? Math.round(confs.reduce((a, b) => a + b, 0) / confs.length) : 0
  return { total: arr.length, go: cnt('go'), watch: cnt('watch'), nogo: cnt('nogo'), avg, aiCount: arr.filter((d) => d.ai_invoked).length }
})

const lastUpdatedLabel = computed(() => {
  void now.value
  return lastUpdated.value ? dayjs(lastUpdated.value).fromNow() : ''
})

onMounted(() => {
  loadDecisions()
  poll = setInterval(loadDecisions, 30000)
  clock = setInterval(() => { now.value = Date.now() }, 30000)
})
onBeforeUnmount(() => {
  if (poll) clearInterval(poll)
  if (clock) clearInterval(clock)
})
</script>

<style scoped>
.scope { display: flex; flex-direction: column; gap: 16px; }

/* filter rail */
.scope-rail { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.segmented { display: inline-flex; background: rgba(8,11,20,0.6); border: 1px solid var(--jd-border); border-radius: 10px; padding: 3px; gap: 2px; }
.seg { font-family: var(--jd-mono); font-size: 12px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--jd-text-muted); background: transparent; border: none; border-radius: 7px; padding: 6px 16px; cursor: pointer; transition: all var(--jd-trans); }
.seg:hover { color: var(--jd-text); }
.seg.active { color: #04121a; background: var(--jd-cyan); font-weight: 600; }
.rail-meta { display: flex; align-items: center; gap: 14px; font-family: var(--jd-mono); font-size: 11px; color: var(--jd-text-muted); }
.rail-meta > span { display: inline-flex; align-items: center; gap: 7px; }

/* feed grid */
.scope-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(400px, 1fr)); gap: 14px; }

/* decision card layout (visuals inherit from global .jd-decision / .jd-gauge / etc) */
.jd-decision { cursor: pointer; }
.d-head { display: flex; align-items: center; gap: 10px; padding: 14px 16px 10px; }
.d-sym { font-size: 19px; font-weight: 600; letter-spacing: -0.01em; color: #fff; }
.d-headspark { width: 88px; height: 22px; margin-left: auto; opacity: 0.9; }
.d-body { display: grid; grid-template-columns: 120px 1fr; gap: 16px; padding: 4px 16px 12px; align-items: center; }
.d-right { min-width: 0; }
.d-nodata { font-family: var(--jd-mono); font-size: 12px; color: var(--jd-text-faint); }
.d-foot { border-top: 1px solid var(--jd-line-2); }
.d-reason { padding: 9px 16px 13px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.d-reason .jd-src { margin-left: auto; white-space: nowrap; }

/* skeleton */
.jd-decision.skeleton { padding: 20px; display: flex; flex-direction: column; gap: 12px; cursor: default; animation: sk 1.6s ease-in-out infinite; }
.sk { border-radius: 6px; background: var(--jd-card-hover); }
.sk-lg { width: 38%; height: 22px; }
.sk-md { width: 62%; height: 14px; }
.sk-block { height: 96px; }
@keyframes sk { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }

@media (max-width: 820px) { .scope-grid { grid-template-columns: 1fr; } }
@media (prefers-reduced-motion: reduce) { .jd-decision.skeleton { animation: none; } }
</style>
