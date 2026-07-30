<template>
  <div>
    <!-- Header -->
    <div class="jd-page-head">
      <div>
        <h1 class="jd-h1">Recommendations</h1>
        <p class="jd-sub">Deterministic engine picks for {{ feed.trade_date || '—' }} · phase reads are <b>descriptive of the current state, not predictions</b></p>
      </div>
      <button type="button" class="jd-btn jd-btn-ghost jd-btn-sm" :disabled="loading" @click="load">
        <i class="pi pi-refresh"></i> Refresh
      </button>
    </div>

    <!-- The honest scoreboard badge -->
    <div class="jd-strip" v-if="feed.oos_badge">
      <div class="jd-strip-item"><div class="l">OOS profit factor</div><div class="v" style="color:var(--jd-cyan)">{{ feed.oos_badge.profit_factor }}</div></div>
      <div class="jd-strip-item"><div class="l">OOS win rate</div><div class="v">{{ Math.round(feed.oos_badge.win_rate * 100) }}<small>%</small></div></div>
      <div class="jd-strip-item"><div class="l">OOS avg R</div><div class="v">{{ feed.oos_badge.avg_r > 0 ? '+' : '' }}{{ feed.oos_badge.avg_r }}</div></div>
      <div class="jd-strip-item" style="flex:2"><div class="l">Source</div><div class="v" style="font-size:12px">{{ feed.oos_badge.source }}</div></div>
    </div>

    <div v-if="error" class="jd-decision nogo" style="padding:24px 22px" role="alert">
      <div class="jd-take" style="margin-bottom:6px"><b>Could not load the feed.</b></div>
      <button type="button" class="jd-btn jd-btn-ghost jd-btn-sm" @click="load">Try again</button>
    </div>

    <div v-else-if="loading && !feed.items.length" class="scope-grid" aria-hidden="true">
      <div v-for="n in 2" :key="n" class="jd-decision skeleton">
        <div class="sk sk-lg"></div><div class="sk sk-md"></div><div class="sk sk-block"></div>
      </div>
    </div>

    <div v-else-if="!shortlist.length && !others.length" class="jd-empty">
      <i class="pi pi-inbox"></i>
      <p>No recommendations published yet.</p>
      <p style="font-size:13px;max-width:460px">The signal cycle publishes after each US close. Come back after ~05:40 MYT on trading days.</p>
    </div>

    <template v-else>
      <!-- Shortlist cards -->
      <h2 class="jd-h2" v-if="shortlist.length">Shortlist — worth a look tomorrow</h2>
      <div class="scope-grid" v-if="shortlist.length">
        <article v-for="r in shortlist" :key="r.symbol" class="jd-decision go" tabindex="0"
                 @click="openSymbol(r.symbol)" @keydown.enter="openSymbol(r.symbol)">
          <div class="d-head">
            <span class="d-sym">#{{ r.rank }} {{ r.symbol }}</span>
            <span class="jd-badge" :class="phaseBadge(r.phase)">{{ phaseLabel(r.phase) }}</span>
            <span class="jd-verdict go">{{ Math.round(r.confidence) }}</span>
          </div>
          <div class="d-body">
            <div class="d-right" style="flex:1">
              <div class="jd-metrics">
                <div class="jd-metric"><div class="k">last price</div><div class="v">{{ fmt(r.features?.price) }}</div></div>
                <div class="jd-metric"><div class="k">stop distance</div><div class="v">{{ fmt(r.features?.stop_distance) }}</div></div>
                <div class="jd-metric"><div class="k">ATR %</div><div class="v">{{ pct(r.features?.atr_pct) }}</div></div>
                <div class="jd-metric"><div class="k">$ vol (20d)</div><div class="v">{{ compact(r.features?.adv) }}</div></div>
                <div class="jd-metric"><div class="k">sector</div><div class="v">{{ r.features?.sector || '—' }}</div></div>
              </div>
            </div>
          </div>
          <div class="d-foot">
            <div class="d-reason">
              <span class="jd-take">{{ r.phase_reason }}</span>
              <span class="jd-src">deterministic · no llm</span>
            </div>
          </div>
        </article>
      </div>

      <!-- Other up-phase names (no rank) -->
      <h2 class="jd-h2" style="margin-top:22px" v-if="others.length">Up-signals that did not make the cut ({{ others.length }})</h2>
      <div class="jd-table-wrap" v-if="others.length">
        <table class="jd-table">
          <thead><tr><th>Symbol</th><th>Confidence</th><th>Phase</th><th>Why it reads that way</th></tr></thead>
          <tbody>
            <tr v-for="r in others" :key="r.symbol" @click="openSymbol(r.symbol)" style="cursor:pointer">
              <td><b>{{ r.symbol }}</b></td>
              <td>{{ Math.round(r.confidence) }}</td>
              <td><span class="jd-badge" :class="phaseBadge(r.phase)">{{ phaseLabel(r.phase) }}</span></td>
              <td style="font-size:12px;color:var(--jd-dim)">{{ r.phase_reason }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { getRecommendations } from '../api/sim'

const router = useRouter()
const feed = ref({ trade_date: null, oos_badge: null, items: [] })
const loading = ref(false)
const error = ref(false)

const shortlist = computed(() => feed.value.items.filter(r => r.rank != null))
const others = computed(() => feed.value.items.filter(r => r.rank == null))

const phaseLabel = p => ({ up: 'uptrend now', down: 'downtrend now', range: 'ranging', unknown: 'insufficient data' }[p] || p)
const phaseBadge = p => ({ up: 'green', down: 'red', range: 'yellow' }[p] || 'cyan')
const fmt = v => (v == null ? '—' : Number(v).toFixed(2))
const pct = v => (v == null ? '—' : (Number(v) * 100).toFixed(1) + '%')
const compact = v => (v == null ? '—' : Intl.NumberFormat('en', { notation: 'compact' }).format(v))
const openSymbol = s => router.push(`/market/${encodeURIComponent(s)}`)

async function load() {
  loading.value = true
  error.value = false
  try {
    const { data } = await getRecommendations()
    feed.value = data
  } catch (e) {
    error.value = true
  } finally {
    loading.value = false
  }
}
onMounted(load)
</script>

<style scoped>
/* Shared page chrome (.jd-page-head, .d-head, …) lives in assets/main.css. */
.scope-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(400px, 1fr)); gap: 14px; }
.jd-decision.skeleton { padding: 20px; display: flex; flex-direction: column; gap: 12px; cursor: default; animation: sk 1.6s ease-in-out infinite; }
.sk { border-radius: 6px; background: var(--jd-card-hover); }
.sk-lg { width: 38%; height: 22px; } .sk-md { width: 62%; height: 14px; } .sk-block { height: 96px; }
@keyframes sk { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
@media (max-width: 820px) { .scope-grid { grid-template-columns: 1fr; } }
@media (prefers-reduced-motion: reduce) { .jd-decision.skeleton { animation: none; } }
</style>
