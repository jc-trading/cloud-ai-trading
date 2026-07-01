<template>
  <div class="feed">
    <!-- Masthead — this page's one job: the latest verdict per symbol, and why -->
    <header class="feed-masthead">
      <div class="masthead-lede">
        <div class="masthead-eyebrow">
          <span class="pulse" :class="{ live: !loading }" aria-hidden="true"></span>
          Decision log
        </div>
        <h1 class="masthead-title">What the analyst is calling right now</h1>
        <p class="masthead-sub">
          One case file per symbol — the data it pulled, how it reasoned, and its
          go / no-go / watch verdict. Everything is on the record.
        </p>
      </div>
      <div class="masthead-actions">
        <router-link to="/portfolio" class="ledger-link">
          Positions &amp; P&amp;L
          <i class="pi pi-arrow-right" aria-hidden="true"></i>
        </router-link>
        <router-link to="/admin/system" class="ledger-link subtle">
          System monitoring
        </router-link>
        <button
          type="button"
          class="refresh"
          :disabled="loading"
          @click="loadDecisions"
        >
          <i class="pi pi-refresh" :class="{ spin: loading }" aria-hidden="true"></i>
          <span>{{ loading ? 'Reading…' : 'Refresh' }}</span>
        </button>
      </div>
    </header>

    <!-- Filter rail: crypto / equity -->
    <div class="feed-rail" role="group" aria-label="Filter decisions by asset class">
      <div class="segmented">
        <button
          v-for="opt in assetFilters"
          :key="opt.value"
          type="button"
          class="seg"
          :class="{ active: assetClass === opt.value }"
          :aria-pressed="assetClass === opt.value"
          @click="setAssetClass(opt.value)"
        >
          {{ opt.label }}
        </button>
      </div>
      <div class="rail-meta">
        <span v-if="!loading && !error">{{ decisions.length }} on file</span>
        <span v-if="lastUpdated" class="rail-time">Read {{ lastUpdatedLabel }}</span>
      </div>
    </div>

    <!-- Error -->
    <div v-if="error" class="state state-error" role="alert">
      <p class="state-title">Could not read the decision log.</p>
      <p class="state-body">{{ error }}</p>
      <button type="button" class="refresh" @click="loadDecisions">Try again</button>
    </div>

    <!-- Loading skeleton -->
    <div v-else-if="loading && !decisions.length" class="feed-grid" aria-hidden="true">
      <div v-for="n in 4" :key="n" class="skeleton-card">
        <div class="sk-line sk-lg"></div>
        <div class="sk-line sk-md"></div>
        <div class="sk-block"></div>
      </div>
    </div>

    <!-- Empty -->
    <div v-else-if="!decisions.length" class="state state-empty">
      <p class="state-title">No verdicts on file yet.</p>
      <p class="state-body">
        The analyst records a case file each cycle it runs. Add symbols to your
        watchlist or trigger an analysis, and their verdicts will land here.
      </p>
      <router-link to="/watchlist" class="ledger-link">
        Open watchlist
        <i class="pi pi-arrow-right" aria-hidden="true"></i>
      </router-link>
    </div>

    <!-- The feed: one dossier card per symbol -->
    <div v-else class="feed-grid">
      <article
        v-for="d in decisions"
        :key="d.id"
        class="dossier"
        :class="`v-${verdictKey(d.verdict)}`"
        @click="openCaseFile(d.symbol)"
      >
        <span class="dossier-spine" aria-hidden="true"></span>

        <!-- Header: symbol + class + verdict stamp -->
        <div class="dossier-head">
          <div class="head-id">
            <span class="symbol">{{ d.symbol }}</span>
            <span class="asset-chip" :class="assetKey(d.asset_class)">
              {{ d.asset_class }}
            </span>
            <span
              v-if="d.position_id"
              class="asset-chip placed"
              title="Order placed on Alpaca paper — position open"
            >
              ● position
            </span>
          </div>
          <span class="stamp" :class="`v-${verdictKey(d.verdict)}`">
            {{ verdictLabel(d.verdict) }}
          </span>
        </div>

        <!-- Action + conviction -->
        <div class="dossier-call">
          <span class="call-action" :class="actionKey(d.action)">{{ String(d.action).toUpperCase() }}</span>
          <div class="conviction" v-if="d.ai_invoked">
            <span class="conviction-label">conviction</span>
            <span class="conviction-track" aria-hidden="true">
              <span class="conviction-fill" :style="{ width: clampPct(d.confidence) + '%' }"></span>
            </span>
            <span class="conviction-num">{{ clampPct(d.confidence) }}%</span>
          </div>
          <span v-else class="call-noai">AI not invoked</span>
          <span class="call-time">{{ formatTime(d.created_at) }}</span>
        </div>

        <!-- Pulled data — the ledger, laid open -->
        <section class="dossier-block" v-if="dataEntries(d).length">
          <div class="block-label">Data pulled</div>
          <dl class="ledger">
            <div v-for="row in dataEntries(d)" :key="row.k" class="ledger-row">
              <dt>{{ row.k }}</dt>
              <dd>{{ row.v }}</dd>
            </div>
          </dl>
          <div v-if="dataOverflow(d) > 0" class="ledger-more">+{{ dataOverflow(d) }} more fields</div>
        </section>
        <section class="dossier-block muted" v-else>
          <div class="block-label">Data pulled</div>
          <p class="block-empty">No indicator snapshot on this cycle.</p>
        </section>

        <!-- Reasoning -->
        <section class="dossier-block">
          <div class="block-label">Reasoning</div>
          <p class="reasoning" v-if="reasoning(d)">{{ reasoning(d) }}</p>
          <p class="reasoning faint" v-else>No reasoning recorded for this verdict.</p>
          <ul class="factors" v-if="keyFactors(d).length">
            <li v-for="(f, i) in keyFactors(d)" :key="i">{{ f }}</li>
          </ul>
          <p class="risk" v-if="riskWarning(d)">
            <i class="pi pi-exclamation-triangle" aria-hidden="true"></i>
            {{ riskWarning(d) }}
          </p>
        </section>

        <!-- Footer: completeness + open case file -->
        <div class="dossier-foot">
          <div class="completeness" :title="completenessTitle(d)">
            <span
              class="dot"
              :class="{ ok: completeness(d).indicators }"
            >indicators</span>
            <span
              class="dot"
              :class="{ ok: completeness(d).ai_output }"
            >AI output</span>
          </div>
          <router-link
            class="case-link"
            :to="{ name: 'SymbolDetail', params: { symbol: d.symbol } }"
            @click.stop
          >
            Open case file
            <i class="pi pi-arrow-right" aria-hidden="true"></i>
          </router-link>
        </div>
      </article>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { getDecisions } from '@/api'
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

const MAX_LEDGER_ROWS = 6

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

const openCaseFile = (symbol) => {
  router.push({ name: 'SymbolDetail', params: { symbol } })
}

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
const actionKey = (a) => String(a || '').toLowerCase()
const assetKey = (c) => (String(c || '').toLowerCase() === 'equity' ? 'equity' : 'crypto')
const clampPct = (n) => {
  const x = Number(n) || 0
  return Math.max(0, Math.min(100, Math.round(x)))
}

// --- Pulled-data ledger (indicators_snapshot / FA) ---
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
    return s.length > 40 ? s.slice(0, 39) + '…' : s
  }
  const s = String(v)
  return s.length > 40 ? s.slice(0, 39) + '…' : s
}
const snapshotEntries = (d) => {
  const snap = d?.indicators_snapshot
  if (!snap || typeof snap !== 'object') return []
  return Object.entries(snap).filter(([, v]) => v !== null && v !== undefined && v !== '')
}
const dataEntries = (d) =>
  snapshotEntries(d)
    .slice(0, MAX_LEDGER_ROWS)
    .map(([k, v]) => ({ k, v: formatVal(v) }))
const dataOverflow = (d) => Math.max(0, snapshotEntries(d).length - MAX_LEDGER_ROWS)

// --- Reasoning ---
const reasoning = (d) => {
  if (d?.verdict_reason) return d.verdict_reason
  const r = d?.claude_response?.reason
  return typeof r === 'string' ? r : ''
}
const keyFactors = (d) => {
  const f = d?.claude_response?.key_factors
  return Array.isArray(f) ? f.filter((x) => typeof x === 'string' && x.trim()).slice(0, 4) : []
}
const riskWarning = (d) => {
  const w = d?.claude_response?.risk_warning
  return typeof w === 'string' && w.trim() ? w : ''
}

// --- Completeness ---
const completeness = (d) => {
  const dc = d?.data_completeness || {}
  return {
    indicators: !!dc.indicators,
    ai_output: dc.ai_output !== undefined ? !!dc.ai_output : !!d?.ai_invoked,
  }
}
const completenessTitle = (d) => {
  const c = completeness(d)
  return `Indicators: ${c.indicators ? 'present' : 'missing'} · AI output: ${c.ai_output ? 'present' : 'missing'}`
}

// --- Time (recomputes against the clock ref so relative labels stay fresh) ---
const formatTime = (ts) => {
  void now.value
  return ts ? dayjs(ts).fromNow() : '—'
}
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
/* ── Verdict ledger tokens — a calm ink/graphite terminal, not a stat dashboard.
   Deliberately warm-neutral (not the app's navy) and muted (no neon), so the
   verdict itself — go / no-go / watch — is the only thing that carries colour. */
.feed {
  --ink:        #0e0f12;
  --graphite:   #16181d;
  --graphite-2: #1b1e24;
  --graphite-h: #1f232a;
  --rule:       rgba(150, 154, 165, 0.10);
  --rule-2:     rgba(150, 154, 165, 0.16);
  --ink-text:   #d8dae0;
  --ink-muted:  #8a8f9a;
  --ink-faint:  #5b606b;

  --go:      #63a986;   --go-bg:   rgba(64, 120, 92, 0.16);   --go-line:   rgba(99, 169, 134, 0.42);
  --nogo:    #c8735a;   --nogo-bg: rgba(150, 68, 48, 0.16);   --nogo-line: rgba(200, 115, 90, 0.42);
  --watch:   #d0a24c;   --watch-bg:rgba(160, 122, 48, 0.15);  --watch-line:rgba(208, 162, 76, 0.40);

  --mono: ui-monospace, 'SF Mono', 'JetBrains Mono', 'Menlo', 'Cascadia Code', monospace;
  --sans: 'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif;

  display: flex;
  flex-direction: column;
  gap: 22px;
  color: var(--ink-text);
  font-family: var(--sans);
}

/* ── Masthead ── */
.feed-masthead {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 24px;
  flex-wrap: wrap;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--rule-2);
}
.masthead-lede { max-width: 640px; }
.masthead-eyebrow {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-family: var(--mono);
  font-size: 0.6875rem;
  letter-spacing: 0.22em;
  text-transform: uppercase;
  color: var(--ink-muted);
  margin-bottom: 12px;
}
.pulse {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--ink-faint);
}
.pulse.live { background: var(--go); box-shadow: 0 0 0 0 var(--go-bg); animation: pulse 2.4s ease-out infinite; }
.masthead-title {
  font-family: var(--mono);
  font-size: 1.5rem;
  font-weight: 600;
  letter-spacing: -0.01em;
  color: #eef0f3;
  margin: 0 0 8px;
  line-height: 1.2;
}
.masthead-sub {
  font-size: 0.9375rem;
  line-height: 1.55;
  color: var(--ink-muted);
  margin: 0;
}
.masthead-actions {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
}
.ledger-link {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-family: var(--mono);
  font-size: 0.8125rem;
  letter-spacing: 0.02em;
  color: var(--ink-text);
  text-decoration: none;
  border-bottom: 1px solid transparent;
  transition: border-color var(--jd-trans, 0.2s), color var(--jd-trans, 0.2s);
}
.ledger-link:hover { border-bottom-color: var(--rule-2); }
.ledger-link.subtle { color: var(--ink-muted); }
.ledger-link i { font-size: 0.6875rem; }

.refresh {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-family: var(--mono);
  font-size: 0.8125rem;
  color: var(--ink-text);
  background: var(--graphite-2);
  border: 1px solid var(--rule-2);
  border-radius: 7px;
  padding: 8px 14px;
  cursor: pointer;
  transition: background var(--jd-trans, 0.2s), border-color var(--jd-trans, 0.2s);
}
.refresh:hover:not(:disabled) { background: var(--graphite-h); border-color: rgba(150,154,165,0.28); }
.refresh:disabled { opacity: 0.55; cursor: default; }
.refresh i.spin { animation: spin 1s linear infinite; }

/* ── Filter rail ── */
.feed-rail {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
.segmented {
  display: inline-flex;
  background: var(--graphite);
  border: 1px solid var(--rule-2);
  border-radius: 8px;
  padding: 3px;
  gap: 2px;
}
.seg {
  font-family: var(--mono);
  font-size: 0.75rem;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--ink-muted);
  background: transparent;
  border: none;
  border-radius: 6px;
  padding: 6px 16px;
  cursor: pointer;
  transition: color var(--jd-trans, 0.2s), background var(--jd-trans, 0.2s);
}
.seg:hover { color: var(--ink-text); }
.seg.active { color: #0e0f12; background: var(--ink-text); font-weight: 600; }
.rail-meta {
  display: flex;
  align-items: center;
  gap: 14px;
  font-family: var(--mono);
  font-size: 0.75rem;
  color: var(--ink-faint);
}
.rail-time { color: var(--ink-faint); }

/* ── Feed grid ── */
.feed-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
  gap: 18px;
}

/* ── Dossier card ── */
.dossier {
  position: relative;
  background: var(--graphite);
  border: 1px solid var(--rule);
  border-radius: 10px;
  padding: 20px 22px 18px 26px;
  overflow: hidden;
  cursor: pointer;
  transition: background var(--jd-trans, 0.2s), border-color var(--jd-trans, 0.2s), transform var(--jd-trans, 0.2s);
}
.dossier:hover { background: var(--graphite-2); border-color: var(--rule-2); }
.dossier-spine {
  position: absolute;
  left: 0; top: 0; bottom: 0;
  width: 4px;
}
.dossier.v-go   .dossier-spine { background: var(--go-line); }
.dossier.v-nogo .dossier-spine { background: var(--nogo-line); }
.dossier.v-watch .dossier-spine { background: var(--watch-line); }

/* header */
.dossier-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  margin-bottom: 14px;
}
.head-id { display: flex; align-items: baseline; gap: 10px; min-width: 0; }
.symbol {
  font-family: var(--mono);
  font-size: 1.375rem;
  font-weight: 600;
  letter-spacing: 0.01em;
  color: #eef0f3;
}
.asset-chip {
  font-family: var(--mono);
  font-size: 0.625rem;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  padding: 2px 7px;
  border-radius: 4px;
  border: 1px solid var(--rule-2);
  color: var(--ink-muted);
}
.asset-chip.equity { color: #a9a2d6; border-color: rgba(150, 140, 210, 0.3); }
.asset-chip.crypto { color: #86b4c2; border-color: rgba(120, 170, 190, 0.3); }
/* Execution state: go-Decision has been placed (paper) and a position is open. */
.asset-chip.placed { color: #7fd1a3; border-color: rgba(120, 200, 150, 0.38); }

.stamp {
  font-family: var(--mono);
  font-size: 0.875rem;
  font-weight: 700;
  letter-spacing: 0.12em;
  padding: 5px 12px;
  border-radius: 5px;
  border: 1px solid;
  white-space: nowrap;
}
.stamp.v-go   { color: var(--go);   background: var(--go-bg);   border-color: var(--go-line); }
.stamp.v-nogo { color: var(--nogo); background: var(--nogo-bg); border-color: var(--nogo-line); }
.stamp.v-watch{ color: var(--watch);background: var(--watch-bg);border-color: var(--watch-line); }

/* call row */
.dossier-call {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  padding-bottom: 16px;
  margin-bottom: 4px;
  border-bottom: 1px dashed var(--rule-2);
}
.call-action {
  font-family: var(--mono);
  font-size: 0.75rem;
  font-weight: 700;
  letter-spacing: 0.1em;
  color: var(--ink-muted);
}
.call-action.buy  { color: var(--go); }
.call-action.sell { color: var(--nogo); }
.conviction { display: flex; align-items: center; gap: 8px; flex: 1; min-width: 140px; }
.conviction-label {
  font-family: var(--mono);
  font-size: 0.625rem;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--ink-faint);
}
.conviction-track {
  flex: 1;
  height: 4px;
  min-width: 48px;
  background: rgba(150, 154, 165, 0.14);
  border-radius: 2px;
  overflow: hidden;
}
.conviction-fill { display: block; height: 100%; background: var(--ink-muted); border-radius: 2px; }
.conviction-num { font-family: var(--mono); font-size: 0.75rem; color: var(--ink-text); }
.call-noai {
  font-family: var(--mono);
  font-size: 0.6875rem;
  letter-spacing: 0.06em;
  color: var(--ink-faint);
  flex: 1;
}
.call-time {
  font-family: var(--mono);
  font-size: 0.6875rem;
  color: var(--ink-faint);
  margin-left: auto;
}

/* blocks */
.dossier-block { padding: 14px 0; border-bottom: 1px solid var(--rule); }
.dossier-block.muted { opacity: 0.75; }
.block-label {
  font-family: var(--mono);
  font-size: 0.625rem;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--ink-faint);
  margin-bottom: 10px;
}
.block-empty { font-size: 0.8125rem; color: var(--ink-faint); margin: 0; }

/* ledger (data pulled) */
.ledger {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 2px 20px;
  margin: 0;
}
.ledger-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  padding: 4px 0;
  border-bottom: 1px dotted var(--rule);
}
.ledger-row dt {
  font-family: var(--mono);
  font-size: 0.75rem;
  color: var(--ink-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.ledger-row dd {
  font-family: var(--mono);
  font-size: 0.8125rem;
  color: var(--ink-text);
  margin: 0;
  text-align: right;
}
.ledger-more {
  font-family: var(--mono);
  font-size: 0.6875rem;
  color: var(--ink-faint);
  margin-top: 8px;
}

/* reasoning */
.reasoning {
  font-size: 0.9375rem;
  line-height: 1.6;
  color: var(--ink-text);
  margin: 0;
}
.reasoning.faint { color: var(--ink-faint); }
.factors {
  list-style: none;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 12px 0 0;
  padding: 0;
}
.factors li {
  font-family: var(--mono);
  font-size: 0.6875rem;
  color: var(--ink-muted);
  background: var(--graphite-2);
  border: 1px solid var(--rule-2);
  border-radius: 4px;
  padding: 3px 8px;
}
.risk {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  font-size: 0.8125rem;
  line-height: 1.5;
  color: var(--watch);
  margin: 12px 0 0;
}
.risk i { font-size: 0.75rem; margin-top: 3px; }

/* footer */
.dossier-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-top: 14px;
}
.completeness { display: flex; gap: 14px; }
.completeness .dot {
  position: relative;
  font-family: var(--mono);
  font-size: 0.625rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--ink-faint);
  padding-left: 13px;
}
.completeness .dot::before {
  content: '';
  position: absolute;
  left: 0; top: 50%;
  transform: translateY(-50%);
  width: 6px; height: 6px;
  border-radius: 50%;
  background: var(--ink-faint);
}
.completeness .dot.ok { color: var(--ink-muted); }
.completeness .dot.ok::before { background: var(--go); }
.case-link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-family: var(--mono);
  font-size: 0.75rem;
  letter-spacing: 0.04em;
  color: var(--ink-text);
  text-decoration: none;
  border-bottom: 1px solid transparent;
  transition: border-color var(--jd-trans, 0.2s);
}
.case-link:hover { border-bottom-color: var(--rule-2); }
.case-link i { font-size: 0.625rem; }

/* ── States ── */
.state {
  border: 1px solid var(--rule-2);
  border-radius: 10px;
  background: var(--graphite);
  padding: 40px 28px;
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}
.state-error { border-color: var(--nogo-line); }
.state-title { font-family: var(--mono); font-size: 1rem; color: var(--ink-text); margin: 0; }
.state-body { font-size: 0.9375rem; line-height: 1.55; color: var(--ink-muted); max-width: 460px; margin: 0 0 8px; }

/* ── Skeleton ── */
.skeleton-card {
  background: var(--graphite);
  border: 1px solid var(--rule);
  border-radius: 10px;
  padding: 22px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.sk-line { height: 14px; border-radius: 4px; background: var(--graphite-h); }
.sk-line.sk-lg { width: 40%; height: 22px; }
.sk-line.sk-md { width: 65%; }
.sk-block { height: 90px; border-radius: 6px; background: var(--graphite-2); margin-top: 6px; }
.skeleton-card { animation: shimmer 1.6s ease-in-out infinite; }

/* ── Focus (keyboard) ── */
.seg:focus-visible,
.refresh:focus-visible,
.ledger-link:focus-visible,
.case-link:focus-visible,
.dossier:focus-visible {
  outline: 2px solid var(--watch);
  outline-offset: 2px;
  border-radius: 6px;
}

/* ── Motion ── */
@keyframes spin { to { transform: rotate(360deg); } }
@keyframes pulse {
  0%   { box-shadow: 0 0 0 0 var(--go-bg); }
  70%  { box-shadow: 0 0 0 6px rgba(99, 169, 134, 0); }
  100% { box-shadow: 0 0 0 0 rgba(99, 169, 134, 0); }
}
@keyframes shimmer { 0%, 100% { opacity: 1; } 50% { opacity: 0.55; } }

@media (prefers-reduced-motion: reduce) {
  .pulse.live, .refresh i.spin, .skeleton-card { animation: none; }
  .dossier, .refresh, .seg, .ledger-link, .case-link { transition: none; }
}

/* ── Responsive ── */
@media (max-width: 820px) {
  .feed-grid { grid-template-columns: 1fr; }
  .feed-masthead { align-items: flex-start; }
}
@media (max-width: 520px) {
  .dossier { padding: 16px 16px 14px 20px; }
  .ledger { grid-template-columns: 1fr; }
  .masthead-title { font-size: 1.25rem; }
  .masthead-actions { width: 100%; }
}
</style>
