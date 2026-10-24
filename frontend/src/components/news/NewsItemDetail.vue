<template>
  <div class="news-detail">
    <div v-if="loading" class="state"><span class="judia-spinner" style="width:26px;height:26px"></span></div>
    <div v-else-if="error" class="jd-alert error" role="alert"><i class="pi pi-times-circle"></i><span>{{ error }}</span></div>
    <template v-else-if="item">
      <div class="head">
        <div class="headline">{{ item.headline }}</div>
        <div class="badges">
          <span :class="['jd-badge', STATUS_COLORS[item.status] || 'gray']">{{ item.status }}</span>
          <span class="jd-chip">{{ item.origin }}</span>
          <span v-if="item.skip_reason" class="jd-chip">{{ item.skip_reason }}</span>
        </div>
      </div>

      <div class="times">
        <div><div class="k">Published</div><div class="v">{{ fmtEt(item.published_at) }}</div></div>
        <div><div class="k">Discovered</div><div class="v">{{ fmtEt(item.discovered_at) }}</div></div>
        <div><div class="k">Effective</div><div class="v">{{ item.effective_date || '—' }}</div></div>
        <div><div class="k">Extracted</div><div class="v">{{ fmtEt(item.extracted_at) }}</div></div>
      </div>

      <section v-if="item.summary">
        <div class="jd-h2">Summary</div>
        <p class="text">{{ item.summary }}</p>
      </section>
      <section v-if="item.llm_feedback">
        <div class="jd-h2">LLM feedback</div>
        <p class="text">{{ item.llm_feedback }}</p>
      </section>

      <section>
        <div class="jd-h2">Symbols</div>
        <div v-for="s in item.symbols" :key="s.id" class="card">
          <div class="card-head">
            <span class="sym">{{ s.symbol }}</span>
            <span v-if="s.relevance" class="jd-chip">{{ s.relevance }}</span>
            <span v-if="s.suggested_action" :class="['jd-badge', ACTION_COLORS[s.suggested_action] || 'gray']">{{ s.suggested_action }}</span>
          </div>
          <template v-if="s.claim_type">
            <div class="claim jd-mono" :class="s.direction === 'down' ? 'price-down' : 'price-up'">
              {{ s.direction === 'down' ? '↓' : '↑' }} p{{ s.probability }} · {{ s.expected_move_pct }}% · {{ s.horizon_days }}d
              <span class="cell-muted">· {{ s.claim_type }}</span>
            </div>
            <div v-if="s.claim_type === 'non_price'" class="line">{{ s.claim_text }}</div>
            <div v-if="s.rationale" class="line">{{ s.rationale }}</div>
            <div v-if="s.action_reason" class="line muted">Reason: {{ s.action_reason }}</div>
            <div class="line muted">Window: {{ s.anchor_session || '—' }} → {{ s.evaluate_after || '—' }}</div>
            <div class="line muted">
              Review: {{ s.review_status }}<template v-if="s.review_at"> · due {{ fmtEt(s.review_at) }}</template>
              <template v-if="s.review_note"> · {{ s.review_note }}</template>
            </div>
            <template v-if="s.outcome">
              <div class="line">
                Outcome: <span :class="['jd-badge', OUTCOME_COLORS[outcomeLabel(s.outcome)]]">{{ outcomeLabel(s.outcome) }}</span>
                <template v-if="s.outcome.status === 'scored'"> · score {{ fmtNum(s.outcome.score) }}</template>
                · {{ s.outcome.method }} · <span class="nowrap">{{ s.outcome.anchor_session }} → {{ s.outcome.end_session }}</span>
              </div>
              <div v-if="s.outcome.realized_move_pct != null" class="line muted jd-mono">
                move {{ fmtPct(s.outcome.realized_move_pct) }} · SPY {{ fmtPct(s.outcome.spy_move_pct) }} · excess {{ fmtPct(s.outcome.excess_move_pct) }}<template v-if="s.outcome.sector_etf"> · {{ s.outcome.sector_etf }} {{ fmtPct(s.outcome.sector_move_pct) }}</template>
              </div>
              <div v-if="s.outcome.verdict" class="line">Verdict: {{ s.outcome.verdict }} · evidence {{ s.outcome.evidence || '—' }}</div>
              <div v-if="s.outcome.reason" class="line muted">{{ s.outcome.reason }}</div>
              <div v-if="!s.outcome.scoring_eligible" class="line muted">Not scored (backfill)</div>
            </template>
            <div v-else class="line muted">Outcome: pending (window ends {{ s.evaluate_after }})</div>
          </template>
          <div v-else class="line muted">No claim</div>
        </div>
        <p v-if="!item.symbols.length" class="d-nodata">No symbols</p>
      </section>

      <section>
        <div class="jd-h2">Sources</div>
        <div v-for="link in item.sources" :key="link.id" class="source">
          <div>
            <a v-if="safeUrl(link.url)" :href="safeUrl(link.url)" target="_blank" rel="noopener noreferrer">{{ link.source.name }} ({{ link.source.kind }})</a>
            <span v-else>{{ link.source.name }} ({{ link.source.kind }})</span>
            <span v-if="!link.source.enabled" class="jd-badge gray off">off</span>
          </div>
          <div class="line muted jd-mono">
            {{ link.provider }} · {{ link.external_id }}
            · {{ link.source.lift != null ? `lift ${Number(link.source.lift).toFixed(2)} (n=${link.source.n_evaluated})` : 'unverified' }}
          </div>
        </div>
        <p v-if="!item.sources.length" class="d-nodata">No sources</p>
      </section>

      <section>
        <div class="jd-h2">LLM</div>
        <div v-if="item.llm" class="jd-mono llm">
          <div>{{ item.llm.model }} · {{ item.llm.platform }} · {{ item.llm.billing }}</div>
          <div>in {{ fmtInt(item.llm.total_input_tokens) }} (uncached {{ fmtInt(item.llm.input_tokens) }} · cache read {{ fmtInt(item.llm.cache_read_tokens) }} · cache write {{ fmtInt(item.llm.cache_creation_tokens) }}) · out {{ fmtInt(item.llm.output_tokens) }}</div>
          <div>≈${{ Number(item.llm.cost_usd ?? 0).toFixed(6) }} for the whole call, shared by {{ item.llm.batch_size }} item(s)</div>
          <div>{{ item.llm.latency_ms != null ? `${item.llm.latency_ms} ms` : '—' }}</div>
        </div>
        <p v-else class="d-nodata">No LLM call</p>
      </section>
    </template>
  </div>
</template>

<script setup>
defineProps({
  item: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  error: { type: [String, null], default: null },
})

const ACTION_COLORS = { buy: 'green', sell: 'red', review: 'yellow', ignore: 'gray' }
const STATUS_COLORS = { extracted: 'green', pending: 'yellow', failed: 'red' }
const OUTCOME_COLORS = { hit: 'green', miss: 'red', 'no data': 'gray', excluded: 'gray' }

const fmtEt = (iso) => {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('en-US', {
    timeZone: 'America/New_York', month: 'short', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }) + ' ET'
}
const fmtInt = (v) => (v ?? 0).toLocaleString()
const fmtNum = (v) => (v == null ? '—' : Number(v).toFixed(2))
const fmtPct = (v) => (v == null ? '—' : `${v > 0 ? '+' : ''}${Number(v).toFixed(2)}%`)
const outcomeLabel = (o) => {
  if (o.status === 'scored') return o.hit ? 'hit' : 'miss'
  return o.status === 'no_data' ? 'no data' : 'excluded'
}
const safeUrl = (u) => (typeof u === 'string' && /^https?:\/\//i.test(u) ? u : null)
</script>

<style scoped>
.news-detail { max-height: 70vh; overflow-y: auto; display: flex; flex-direction: column; gap: 16px; }
.state { display: flex; justify-content: center; padding: 32px; }
.head { display: flex; flex-direction: column; gap: 8px; }
.headline { font-size: 15px; font-weight: 600; color: var(--jd-text); line-height: 1.4; overflow-wrap: anywhere; }
.badges { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.times { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 8px 12px; }
.times .k { font-family: var(--jd-mono); font-size: 9px; color: var(--jd-text-faint); letter-spacing: 0.08em; text-transform: uppercase; }
.times .v { font-family: var(--jd-mono); font-size: 12px; color: var(--jd-text); margin-top: 1px; }
.text { margin: 0; font-size: 13px; line-height: 1.5; color: var(--jd-text); overflow-wrap: anywhere; }
.card { border: 1px solid var(--jd-line-2); border-radius: 10px; padding: 10px 12px; margin-bottom: 8px; }
.card-head { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-bottom: 6px; }
.sym { font-weight: 600; color: #fff; }
.claim { font-size: 12.5px; margin-bottom: 4px; }
.line { font-size: 12.5px; line-height: 1.5; color: var(--jd-text); overflow-wrap: anywhere; }
.muted, .cell-muted { color: var(--jd-text-muted); }
.source { padding: 6px 0; border-bottom: 1px solid var(--jd-line-2); font-size: 13px; overflow-wrap: anywhere; }
.source:last-child { border-bottom: none; }
.source a { color: var(--jd-cyan); }
.off { margin-left: 6px; }
.llm { font-size: 12px; line-height: 1.7; color: var(--jd-text); overflow-wrap: anywhere; }
.nowrap { white-space: nowrap; }
</style>
