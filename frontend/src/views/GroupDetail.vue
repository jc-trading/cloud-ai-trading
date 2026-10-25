<template>
  <div class="jd-page">
    <div class="jd-page-head" style="margin-bottom:0">
      <div>
        <h1 class="jd-h1">
          {{ group?.name || 'Strategy group' }}
          <span v-if="group" :class="['jd-badge', statusColor(group.status)]" style="vertical-align:middle">{{ group.status }}</span>
        </h1>
        <p v-if="group" class="jd-sub">
          {{ group.strategy_key }} · created {{ fmtTime(group.created_at) }}
          <span v-if="group.archived_at"> · archived {{ fmtTime(group.archived_at) }}</span>
        </p>
      </div>
      <div class="head-actions">
        <template v-if="group && auth.isAdmin">
          <button v-if="group.status === 'active'" type="button" class="jd-btn jd-btn-ghost jd-btn-sm" @click="askStatus('paused')">
            <i class="pi pi-pause"></i> Pause
          </button>
          <template v-if="group.status === 'paused'">
            <button type="button" class="jd-btn jd-btn-success jd-btn-sm" @click="askStatus('active')">
              <i class="pi pi-play"></i> Activate
            </button>
            <button type="button" class="jd-btn jd-btn-danger jd-btn-sm" @click="askStatus('archived')">
              <i class="pi pi-inbox"></i> Archive
            </button>
          </template>
          <router-link :to="`/groups/${id}/clone`" class="jd-btn jd-btn-ghost jd-btn-sm">
            <i class="pi pi-copy"></i> Clone
          </router-link>
        </template>
        <router-link v-if="group" :to="`/compare?group_ids=${id}`" class="jd-btn jd-btn-ghost jd-btn-sm">
          <i class="pi pi-chart-line"></i> Compare live vs backtests
        </router-link>
      </div>
    </div>

    <div v-if="error" class="jd-decision nogo" style="padding:24px 22px" role="alert">
      <div class="jd-take" style="margin-bottom:6px"><b>{{ error }}</b></div>
      <button type="button" class="jd-btn jd-btn-ghost jd-btn-sm" @click="load">Try again</button>
    </div>
    <div v-else-if="loading && !group" class="jd-empty"><p>Loading…</p></div>

    <template v-if="group">
      <div class="cards-2">
        <section class="jd-card">
          <div class="jd-card-header"><h2 class="jd-card-title">Config</h2></div>
          <div class="jd-card-body config">
            <div>
              <div class="jd-h2">params_json</div>
              <pre class="jd-mono">{{ JSON.stringify(group.params_json, null, 2) }}</pre>
            </div>
            <div>
              <div class="jd-h2">screener_json</div>
              <p v-if="isEmpty(group.screener_json)" class="d-nodata">legacy v1 pool</p>
              <pre v-else class="jd-mono">{{ JSON.stringify(group.screener_json, null, 2) }}</pre>
            </div>
            <div>
              <div class="jd-h2">trade_settings_json</div>
              <p v-if="isEmpty(group.trade_settings_json)" class="d-nodata">{{ group.timeframe === '1m' ? '1m defaults (scalping, RTH only)' : 'v1 defaults (swing; no limit / take-profit / max hold)' }}</p>
              <pre v-else class="jd-mono">{{ JSON.stringify(group.trade_settings_json, null, 2) }}</pre>
            </div>
          </div>
        </section>

        <section class="jd-card">
          <div class="jd-card-header"><h2 class="jd-card-title">Account</h2></div>
          <div class="jd-card-body">
            <div class="jd-metrics">
              <div class="jd-metric"><div class="k">start</div><div class="v">{{ money(group.account.starting_capital) }}</div></div>
              <div class="jd-metric"><div class="k">cash</div><div class="v">{{ money(group.account.cash) }}</div></div>
              <div class="jd-metric"><div class="k">open lots</div><div class="v">{{ group.account.open_positions }}</div></div>
              <div class="jd-metric"><div class="k">entry mode</div><div class="v">{{ group.account.entry_mode ?? '—' }}</div></div>
              <div class="jd-metric">
                <div class="k">last equity</div>
                <div class="v">{{ group.account.last_snapshot ? money(group.account.last_snapshot.equity) : '—' }}</div>
              </div>
              <div class="jd-metric">
                <div class="k">snapshot date</div>
                <div class="v">{{ group.account.last_snapshot?.date ?? '—' }}</div>
              </div>
            </div>
          </div>
        </section>
      </div>

      <section class="jd-card">
        <div class="jd-card-header"><h2 class="jd-card-title">Cycle runs — last 30 days</h2></div>
        <div class="jd-card-body">
          <DataTable
            :columns="runColumns"
            :data="runs"
            row-key="id"
            :page-size="10"
            :loading="runsLoading"
            :error="runsError"
          >
            <template #cell:created_at="{ value }">
              <span class="jd-mono" style="color: var(--jd-text-muted)">{{ fmtTime(value) }}</span>
            </template>
            <template #cell:outcome="{ value }">
              <span :class="['jd-badge', outcomeColor(value)]">{{ value }}</span>
            </template>
            <template #cell:meta="{ row }">
              <span class="jd-mono" style="font-size:11px" :title="JSON.stringify(row.meta ?? {})">{{ metaText(row.meta) }}</span>
            </template>
            <template #empty>
              <div class="jd-empty"><i class="pi pi-history"></i><p>No cycle runs in the last 30 days.</p></div>
            </template>
          </DataTable>
        </div>
      </section>

      <GroupBacktests :group-id="id" :is-admin="auth.isAdmin" :timeframe="group.timeframe" />

      <section v-if="!isEmpty(group.screener_json)" class="jd-card">
        <div class="jd-card-header flex-header">
          <h2 class="jd-card-title">Pool preview</h2>
          <button type="button" class="jd-btn jd-btn-ghost jd-btn-sm" :disabled="poolLoading" @click="previewPool">
            <i class="pi" :class="poolLoading ? 'pi-spin pi-spinner' : 'pi-filter'"></i>
            {{ poolLoading ? 'Running screener… up to a minute' : 'Preview pool' }}
          </button>
        </div>
        <div class="jd-card-body">
          <div v-if="poolError" class="jd-alert error" role="alert">
            <i class="pi pi-exclamation-triangle"></i> {{ poolError }}
          </div>
          <template v-else-if="pool">
            <div class="jd-metrics" style="margin-bottom:14px">
              <div class="jd-metric"><div class="k">as of</div><div class="v">{{ pool.as_of }}</div></div>
              <div class="jd-metric"><div class="k">evaluated</div><div class="v">{{ pool.evaluated }}</div></div>
              <div class="jd-metric">
                <div class="k">pool / cap</div>
                <div class="v">
                  {{ pool.pool_size }} / {{ pool.pool_cap }}
                  <span v-if="pool.over_cap" class="jd-badge red">over cap</span>
                </div>
              </div>
            </div>
            <div class="jd-table-wrap">
              <table class="jd-table">
                <thead>
                  <tr><th>#</th><th>Field</th><th>Window</th><th>Op</th><th class="th-right">Value</th>
                    <th class="th-right">Passed</th><th class="th-right">Null</th><th class="th-right">Cumulative</th></tr>
                </thead>
                <tbody>
                  <tr v-for="r in pool.rules" :key="r.index">
                    <td>{{ r.index }}</td><td>{{ r.field }}</td><td>{{ r.window ?? '—' }}</td><td>{{ r.op }}</td>
                    <td class="td-right">{{ r.value }}</td><td class="td-right">{{ r.passed }}</td>
                    <td class="td-right">{{ r.null }}</td><td class="td-right">{{ r.passed_cumulative }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p class="jd-src pool-lists">
              include added: {{ pool.include_added.join(', ') || '—' }} ·
              excluded removed: {{ pool.excluded_removed.join(', ') || '—' }} ·
              held added: {{ pool.held_added.join(', ') || '—' }}
            </p>
            <div class="chips">
              <span v-for="s in pool.pool" :key="s" class="jd-chip">{{ s }}</span>
            </div>
          </template>
          <p v-else class="d-nodata">Runs this group's screener on the latest complete session. Click only when needed — it is heavy on the server.</p>
        </div>
      </section>
    </template>

    <Modal v-model="showConfirm" :title="confirm.title" width="520px">
      <p class="confirm-text">{{ confirm.text }}</p>
      <div v-if="confirmError" class="jd-alert error" role="alert" style="margin-top:12px">
        <i class="pi pi-exclamation-triangle"></i> {{ confirmError }}
      </div>
      <template #footer>
        <button type="button" class="jd-btn jd-btn-ghost jd-btn-sm" @click="showConfirm = false">Cancel</button>
        <button type="button" class="jd-btn jd-btn-sm" :class="confirm.status === 'archived' ? 'jd-btn-danger' : 'jd-btn-primary'"
                :disabled="confirming" @click="applyStatus">
          <i v-if="confirming" class="pi pi-spin pi-spinner"></i> {{ confirm.title }}
        </button>
      </template>
    </Modal>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, watch } from 'vue'
import DataTable from '@/components/common/DataTable.vue'
import Modal from '@/components/common/Modal.vue'
import GroupBacktests from '@/components/groups/GroupBacktests.vue'
import { getStrategyGroup, getGroupRuns, setGroupStatus, getGroupScreen } from '@/api/strategyGroups'
import { useAuthStore } from '@/stores/auth'
import { useStrategyGroupsStore } from '@/stores/strategyGroups'
import { useToast } from '@/composables/useToast'
import { detailOf } from '@/composables/useApiError'

const props = defineProps({
  id: { type: String, required: true },
})

const auth = useAuthStore()
const toast = useToast()

const group = ref(null)
const loading = ref(false)
const error = ref(null)
const runs = ref([])
const runsLoading = ref(false)
const runsError = ref(null)
const pool = ref(null)
const poolLoading = ref(false)
const poolError = ref(null)

const runColumns = [
  { key: 'created_at', header: 'Time', sortable: true },
  { key: 'session_date', header: 'Session', sortable: true },
  { key: 'cycle', header: 'Cycle', filterable: true, filterLabel: 'Cycle' },
  { key: 'seq', header: 'Seq', align: 'right' },
  { key: 'outcome', header: 'Outcome', align: 'center', filterable: true, filterLabel: 'Outcome' },
  { key: 'meta', header: 'Meta' },
]

const statusColor = (s) => ({ active: 'green', paused: 'yellow', archived: 'gray' }[s] || 'gray')
const outcomeColor = (o) => ({ ok: 'green', skipped: 'gray', blocked: 'yellow', fail_closed: 'orange', error: 'red' }[o] || 'gray')
const isEmpty = (o) => !o || Object.keys(o).length === 0
const money = (v) => (v == null ? '—' : `$${Number(v).toFixed(2)}`)

const fmtTime = (iso) => {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

const metaText = (meta) => {
  const s = JSON.stringify(meta ?? {})
  return s.length > 120 ? `${s.slice(0, 120)}…` : s
}

async function load() {
  const id = props.id
  loading.value = true
  error.value = null
  try {
    const { data } = await getStrategyGroup(id)
    if (id === props.id) group.value = data
  } catch (e) {
    if (id === props.id) error.value = detailOf(e, 'Failed to load the strategy group')
  } finally {
    if (id === props.id) loading.value = false
  }
}

async function loadRuns() {
  const id = props.id
  runsLoading.value = true
  runsError.value = null
  try {
    const { data } = await getGroupRuns(id, { days: 30 })
    if (id === props.id) runs.value = data
  } catch (e) {
    if (id === props.id) runsError.value = detailOf(e, 'Failed to load cycle runs')
  } finally {
    if (id === props.id) runsLoading.value = false
  }
}

async function previewPool() {
  if (poolLoading.value) return
  const id = props.id
  poolLoading.value = true
  poolError.value = null
  try {
    const { data } = await getGroupScreen(id)
    if (id === props.id) pool.value = data
  } catch (e) {
    if (id === props.id) poolError.value = detailOf(e, 'Screener run failed')
  } finally {
    poolLoading.value = false
  }
}

const showConfirm = ref(false)
const confirming = ref(false)
const confirmError = ref(null)
const confirm = reactive({ status: null, title: '', text: '' })

function askStatus(status) {
  const name = group.value.name
  const copy = {
    active: ['Activate', group.value.timeframe === '1m'
      ? `Activate ${name}? It takes ${group.value.params_json?.rank?.watch_max} of the watch budget's symbols. The nightly task builds its shortlist, the stream subscribes it, and the intraday engine trades it during RTH.`
      : `Activate ${name}? From the next signal cycle it publishes recommendations and books entries on its own account.`],
    paused: ['Pause', `Pause ${name}? It stops publishing recommendations and booking entries. Its open lots keep their stops and exits.`],
    archived: ['Archive', `Archive ${name}? This is final. It needs a flat book and must be outside market hours (RTH + 10 min).`],
  }[status]
  Object.assign(confirm, { status, title: copy[0], text: copy[1] })
  confirmError.value = null
  showConfirm.value = true
}

async function applyStatus() {
  if (confirming.value) return
  confirming.value = true
  confirmError.value = null
  try {
    await setGroupStatus(props.id, confirm.status)
    showConfirm.value = false
    await load()
    await useStrategyGroupsStore().ensureLoaded(true)
  } catch (e) {
    confirmError.value = detailOf(e, 'Status change failed')
    toast.error(confirmError.value)
    load()
  } finally {
    confirming.value = false
  }
}

onMounted(() => { load(); loadRuns() })
watch(() => props.id, () => {
  group.value = null
  runs.value = []
  pool.value = null
  poolError.value = null
  load()
  loadRuns()
})
</script>

<style scoped>
.head-actions { display: flex; align-items: flex-end; gap: 10px; flex-wrap: wrap; justify-content: flex-end; }
.cards-2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 18px; }
.config { display: flex; flex-direction: column; gap: 14px; }
.config pre { margin: 0; font-size: 11.5px; color: var(--jd-text); background: var(--jd-input); border: 1px solid var(--jd-line-2); border-radius: 8px; padding: 10px 12px; max-height: 260px; overflow: auto; }
.flex-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.pool-lists { margin: 12px 0 8px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.confirm-text { margin: 0; font-size: 13.5px; line-height: 1.5; color: var(--jd-text); }
@media (max-width: 560px) {
  .cards-2 { grid-template-columns: 1fr; }
}
</style>
