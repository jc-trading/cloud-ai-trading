<template>
  <div class="jd-page">
    <div class="jd-page-head" style="margin-bottom:0">
      <div>
        <h1 class="jd-h1">Compare</h1>
      </div>
    </div>

    <div v-if="loadError" class="jd-decision nogo" style="padding:24px 22px" role="alert">
      <div class="jd-take" style="margin-bottom:6px"><b>{{ loadError }}</b></div>
      <button type="button" class="jd-btn jd-btn-ghost jd-btn-sm" @click="loadLists">Try again</button>
    </div>

    <section class="jd-card">
      <div class="jd-card-body pickers">
        <div>
          <div class="jd-h2">Groups (live)</div>
          <p v-if="!groups.groups.length" class="d-nodata">{{ groups.loading ? 'Loading…' : 'No strategy groups.' }}</p>
          <label v-for="g in groups.groups" :key="g.id" class="pick">
            <input v-model="groupIds" type="checkbox" :value="g.id" /> {{ g.name }} ({{ g.status }})
          </label>
        </div>
        <div>
          <div class="jd-h2">Backtest runs (succeeded)</div>
          <p v-if="!runs.length" class="d-nodata">{{ listLoading ? 'Loading…' : 'No succeeded runs.' }}</p>
          <label v-for="r in runs" :key="r.id" class="pick">
            <input v-model="runIds" type="checkbox" :value="r.id" /> {{ runLabel(r) }}
          </label>
        </div>
        <div class="compare-side">
          <label class="jd-label" for="compare-start">Start</label>
          <input id="compare-start" v-model="start" class="jd-input jd-mono" type="date" />
          <div class="jd-src" style="margin-top:3px">applies to live accounts only</div>
          <div class="compare-action">
            <button type="button" class="jd-btn jd-btn-primary jd-btn-sm" :disabled="!canCompare || comparing" @click="compare">
              <i class="pi" :class="comparing ? 'pi-spin pi-spinner' : 'pi-chart-line'"></i> Compare
            </button>
            <span class="jd-mono" :style="{ color: selectedCount > MAX ? 'var(--jd-red)' : 'var(--jd-text-muted)', fontSize: '12px' }">
              {{ selectedCount }}/{{ MAX }}
            </span>
          </div>
        </div>
      </div>
    </section>

    <div v-if="compareError" class="jd-alert error" role="alert">
      <i class="pi pi-exclamation-triangle"></i> {{ compareError }}
    </div>

    <template v-if="result">
      <section class="jd-card">
        <div class="jd-card-header"><h2 class="jd-card-title">Equity (rebased to 100)</h2></div>
        <div class="jd-card-body">
          <EquityOverlay :series="equitySeries" />
        </div>
      </section>

      <section class="jd-card">
        <div class="jd-card-header"><h2 class="jd-card-title">Metrics</h2></div>
        <div class="jd-card-body">
          <div class="jd-table-wrap">
            <table class="jd-table">
              <thead>
                <tr>
                  <th>Item</th><th>Type</th>
                  <th class="th-right">PF</th><th class="th-right">Sharpe</th><th class="th-right">max DD</th>
                  <th class="th-right">Turnover</th><th class="th-right">Win</th><th class="th-right">n</th>
                  <th>Source</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="it in result.items" :key="`${it.kind}:${it.id}`">
                  <td>{{ it.label }}</td>
                  <td>
                    <span v-if="it.kind === 'live'" class="jd-badge green">live-forward</span>
                    <span v-else class="jd-badge blue">backtest</span>
                    <span v-if="it.survivorship_biased" class="jd-badge orange" style="margin-left:6px">survivorship-biased</span>
                  </td>
                  <template v-if="it.metrics">
                    <td class="td-right">{{ num(it.metrics.profit_factor) }}</td>
                    <td class="td-right">{{ num(it.metrics.sharpe) }}</td>
                    <td class="td-right">{{ pct(it.metrics.max_drawdown) }}</td>
                    <td class="td-right">{{ num(it.metrics.turnover) }}</td>
                    <td class="td-right">{{ pct(it.metrics.win_rate) }}</td>
                    <td class="td-right">{{ it.metrics.num_trades ?? '—' }}</td>
                  </template>
                  <td v-else colspan="6" style="color: var(--jd-text-muted)">{{ it.note }}</td>
                  <td><span class="jd-src">{{ it.source }} · {{ it.universe_label }}</span></td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import EquityOverlay from '@/components/charts/EquityOverlay.vue'
import { listBacktests, compareBacktests } from '@/api/backtests'
import { useStrategyGroupsStore } from '@/stores/strategyGroups'
import { detailOf } from '@/composables/useApiError'

const MAX = 8

const route = useRoute()
const groups = useStrategyGroupsStore()

const runs = ref([])
const listLoading = ref(false)
const loadError = ref(null)
const groupIds = ref([])
const runIds = ref([])
const start = ref('')
const comparing = ref(false)
const compareError = ref(null)
const result = ref(null)

const selectedCount = computed(() => groupIds.value.length + runIds.value.length)
const canCompare = computed(() => selectedCount.value > 0 && selectedCount.value <= MAX)

const num = (v) => (v == null ? '—' : Number(v).toFixed(2))
const pct = (v) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`)

const groupName = (id) => groups.groups.find((g) => g.id === id)?.name || id
const runLabel = (r) => `${groupName(r.group_id)} · ${r.kind} · ${r.data_start}..${r.data_end}`

const equitySeries = computed(() => (result.value?.items || []).map((it) => ({
  id: `${it.kind}:${it.id}`, label: it.label, dates: it.equity?.dates || [], values: it.equity?.values || [],
})))

const idsFrom = (raw) => (typeof raw === 'string' ? raw.split(',').map((s) => s.trim()).filter(Boolean) : [])

async function loadLists() {
  listLoading.value = true
  loadError.value = null
  try {
    const [, list] = await Promise.all([
      groups.ensureLoaded(),
      listBacktests({ status: 'succeeded', limit: 200 }),
    ])
    runs.value = list.data
    if (groups.error) loadError.value = groups.error
  } catch (e) {
    loadError.value = detailOf(e, 'Failed to load groups and runs')
  } finally {
    listLoading.value = false
  }
}

async function compare() {
  if (comparing.value || !canCompare.value) return
  comparing.value = true
  compareError.value = null
  try {
    const { data } = await compareBacktests({ groupIds: groupIds.value, runIds: runIds.value, start: start.value })
    result.value = data
  } catch (e) {
    compareError.value = detailOf(e, 'Compare failed')
  } finally {
    comparing.value = false
  }
}

onMounted(() => {
  groupIds.value = idsFrom(route.query.group_ids)
  runIds.value = idsFrom(route.query.run_ids)
  loadLists()
  if (selectedCount.value > 0) compare()
})
</script>

<style scoped>
.pickers { display: grid; grid-template-columns: 1fr 1.4fr 240px; gap: 20px; align-items: start; }
.pick { display: flex; align-items: center; gap: 8px; font-size: 13px; padding: 3px 0; cursor: pointer; }
.compare-action { display: flex; align-items: center; gap: 10px; margin-top: 14px; }
@media (max-width: 900px) {
  .pickers { grid-template-columns: 1fr; }
}
</style>
