<template>
  <section class="jd-card">
    <div class="jd-card-header flex-header">
      <h2 class="jd-card-title">Backtests</h2>
      <button type="button" class="jd-btn jd-btn-ghost jd-btn-sm" :disabled="loading" title="Refresh" @click="load">
        <i class="pi pi-refresh" :class="{ 'pi-spin': loading }"></i>
      </button>
    </div>
    <div class="jd-card-body backtests">
      <form v-if="isAdmin" class="new-run" novalidate @submit.prevent="ask">
        <div v-if="formError" class="jd-alert error" role="alert">
          <i class="pi pi-exclamation-triangle"></i> {{ formError }}
        </div>
        <div class="run-fields">
          <div>
            <label class="jd-label" for="bt-kind">Kind</label>
            <select id="bt-kind" v-model="form.kind" class="jd-input jd-select">
              <option value="single">single</option>
              <option value="walk_forward">walk_forward</option>
            </select>
            <p v-if="fieldErrors.kind" class="field-error">{{ fieldErrors.kind }}</p>
          </div>
          <div>
            <label class="jd-label" for="bt-start">Start</label>
            <input id="bt-start" v-model="form.start" class="jd-input jd-mono" type="date" />
            <div v-if="timeframe === '1m'" class="jd-src" style="margin-top:3px">1m: inside the 1min backfill horizon</div>
            <p v-if="fieldErrors.start" class="field-error">{{ fieldErrors.start }}</p>
          </div>
          <div>
            <label class="jd-label" for="bt-end">End</label>
            <input id="bt-end" v-model="form.end" class="jd-input jd-mono" type="date" required />
            <div class="jd-src" style="margin-top:3px">≤ last closed session</div>
            <p v-if="fieldErrors.end" class="field-error">{{ fieldErrors.end }}</p>
          </div>
          <template v-if="form.kind === 'walk_forward' && timeframe !== '1m'">
            <div>
              <label class="jd-label" for="bt-is">IS years</label>
              <input id="bt-is" v-model.number="form.is_years" class="jd-input jd-mono" type="number" step="1" min="1" max="5" />
              <div class="jd-src" style="margin-top:3px">[1, 5]</div>
              <p v-if="fieldErrors.is_years" class="field-error">{{ fieldErrors.is_years }}</p>
            </div>
            <div>
              <label class="jd-label" for="bt-oos">OOS years</label>
              <input id="bt-oos" v-model.number="form.oos_years" class="jd-input jd-mono" type="number" step="1" min="1" max="2" />
              <div class="jd-src" style="margin-top:3px">[1, 2]</div>
              <p v-if="fieldErrors.oos_years" class="field-error">{{ fieldErrors.oos_years }}</p>
            </div>
          </template>
          <template v-if="form.kind === 'walk_forward' && timeframe === '1m'">
            <div>
              <label class="jd-label" for="bt-is-sessions">IS sessions</label>
              <input id="bt-is-sessions" v-model.number="form.is_sessions" class="jd-input jd-mono" type="number" step="1" min="20" max="250" />
              <div class="jd-src" style="margin-top:3px">[20, 250]</div>
              <p v-if="fieldErrors.is_sessions" class="field-error">{{ fieldErrors.is_sessions }}</p>
            </div>
            <div>
              <label class="jd-label" for="bt-oos-sessions">OOS sessions</label>
              <input id="bt-oos-sessions" v-model.number="form.oos_sessions" class="jd-input jd-mono" type="number" step="1" min="10" max="120" />
              <div class="jd-src" style="margin-top:3px">[10, 120]</div>
              <p v-if="fieldErrors.oos_sessions" class="field-error">{{ fieldErrors.oos_sessions }}</p>
            </div>
          </template>
          <div class="run-submit">
            <button type="submit" class="jd-btn jd-btn-primary jd-btn-sm" :disabled="queueing">
              <i class="pi pi-plus"></i> New backtest
            </button>
          </div>
        </div>
        <p v-if="fieldErrors.grid" class="field-error">{{ fieldErrors.grid }}</p>
      </form>

      <DataTable
        :columns="columns"
        :data="runs"
        row-key="id"
        :page-size="10"
        clickable-rows
        :loading="loading"
        :error="error"
        @row-click="(row) => router.push('/backtests/' + row.id)"
      >
        <template #cell:created_at="{ value }">
          <span class="jd-mono" style="color: var(--jd-text-muted)">{{ fmtTime(value) }}</span>
        </template>
        <template #cell:status="{ value }">
          <span :class="['jd-badge', statusColor(value)]">{{ value }}</span>
        </template>
        <template #cell:survivorship="{ row }">
          <span v-if="row.survivorship_biased" class="jd-badge orange">survivorship-biased</span>
          <span v-else class="jd-badge gray">{{ row.universe_label }}</span>
        </template>
        <template #cell:finished_at="{ value }">
          <span class="jd-mono" style="color: var(--jd-text-muted)">{{ fmtTime(value) }}</span>
        </template>
        <template #empty>
          <div class="jd-empty"><i class="pi pi-chart-line"></i><p>No backtests for this group yet.</p></div>
        </template>
      </DataTable>
    </div>

    <Modal v-model="showConfirm" title="Queue backtest" width="520px">
      <p class="confirm-text">
        Queue a {{ form.kind }} backtest for {{ form.start }}..{{ form.end }}? It runs on the research worker; walk-forward runs can take hours.
      </p>
      <template #footer>
        <button type="button" class="jd-btn jd-btn-ghost jd-btn-sm" @click="showConfirm = false">Cancel</button>
        <button type="button" class="jd-btn jd-btn-primary jd-btn-sm" :disabled="queueing" @click="queue">
          <i v-if="queueing" class="pi pi-spin pi-spinner"></i> Queue
        </button>
      </template>
    </Modal>
  </section>
</template>

<script setup>
import { ref, reactive, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import DataTable from '@/components/common/DataTable.vue'
import Modal from '@/components/common/Modal.vue'
import { listBacktests, createBacktest } from '@/api/backtests'
import { detailOf, fieldErrorsOf } from '@/composables/useApiError'
import { backtestBody, backtestFieldOf } from '@/composables/useIntraday'

const props = defineProps({
  groupId: { type: String, required: true },
  isAdmin: { type: Boolean, default: false },
  timeframe: { type: String, default: '1d' },
})

const router = useRouter()

const runs = ref([])
const loading = ref(false)
const error = ref(null)
const form = reactive({ kind: 'single', start: props.timeframe === '1m' ? '' : '2016-01-01', end: '', is_years: 3, oos_years: 1, is_sessions: 60, oos_sessions: 20 })
const showConfirm = ref(false)
const queueing = ref(false)
const formError = ref(null)
const fieldErrors = ref({})

const columns = [
  { key: 'created_at', header: 'Created', sortable: true },
  { key: 'kind', header: 'Kind', filterable: true, filterLabel: 'Kind' },
  { key: 'status', header: 'Status', align: 'center', filterable: true, filterLabel: 'Status' },
  { key: 'range', header: 'Range', accessor: (r) => `${r.data_start}..${r.data_end}` },
  { key: 'survivorship', header: 'Universe', accessor: (r) => r.universe_label },
  { key: 'finished_at', header: 'Finished', sortable: true },
]

const statusColor = (s) => ({ queued: 'gray', running: 'blue', succeeded: 'green', failed: 'red', cancelled: 'gray' }[s] || 'gray')

const fmtTime = (iso) => {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

async function load() {
  const groupId = props.groupId
  loading.value = true
  error.value = null
  try {
    const { data } = await listBacktests({ groupId, limit: 50 })
    if (groupId === props.groupId) runs.value = data
  } catch (e) {
    if (groupId === props.groupId) error.value = detailOf(e, 'Failed to load backtests')
  } finally {
    if (groupId === props.groupId) loading.value = false
  }
}

function ask() {
  formError.value = null
  fieldErrors.value = {}
  showConfirm.value = true
}

async function queue() {
  if (queueing.value) return
  queueing.value = true
  const body = backtestBody(form, props.timeframe, props.groupId)
  try {
    const { data } = await createBacktest(body)
    showConfirm.value = false
    router.push('/backtests/' + data.id)
  } catch (e) {
    showConfirm.value = false
    formError.value = detailOf(e, 'Could not queue the backtest')
    fieldErrors.value = fieldErrorsOf(e, backtestFieldOf)
  } finally {
    queueing.value = false
  }
}

onMounted(load)
watch(() => props.groupId, () => { runs.value = []; load() })
</script>

<style scoped>
.flex-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.backtests { display: flex; flex-direction: column; gap: 16px; }
.new-run { display: flex; flex-direction: column; gap: 10px; }
.run-fields { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 10px 14px; align-items: start; }
.run-submit { align-self: end; padding-bottom: 2px; }
.confirm-text { margin: 0; font-size: 13.5px; line-height: 1.5; color: var(--jd-text); }
.field-error { color: var(--jd-red); font-size: 12px; margin: 4px 0 0; }
</style>
