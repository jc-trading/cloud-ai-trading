<template>
  <div class="jd-page">
    <div class="jd-page-head" style="margin-bottom:0">
      <div>
        <h1 class="jd-h1">News Sources</h1>
        <p class="jd-sub">
          Verified when n ≥ {{ thresholds.min_n ?? '—' }} and lift ≥ {{ thresholds.action_min_lift ?? '—' }}
          · auto-disable below lift {{ thresholds.disable_below_lift ?? '—' }} for {{ thresholds.consecutive_days ?? '—' }} days
        </p>
        <p class="jd-sub">Scores and auto-disable start with P7a; until then every source is unverified.</p>
      </div>
      <div class="head-actions">
        <button type="button" class="jd-btn jd-btn-ghost jd-btn-sm" :disabled="loading" @click="load">
          <i class="pi pi-refresh"></i> Refresh
        </button>
      </div>
    </div>

    <DataTable
      :columns="columns"
      :data="sources"
      row-key="id"
      :searchable="['name']"
      search-placeholder="Search source name…"
      :page-size="25"
      :loading="loading"
      :error="error"
      empty-text="No news sources yet"
    >
      <template #cell:name="{ value }">
        <span class="font-semibold">{{ value }}</span>
      </template>
      <template #cell:state="{ row, value }">
        <span :class="['jd-badge', row.enabled ? 'green' : 'gray']">{{ value }}<template v-if="row.disabled_by"> · {{ row.disabled_by }}</template></span>
      </template>
      <template #cell:score="{ value }">
        <span class="jd-mono">{{ fmtNum(value) }}</span>
      </template>
      <template #cell:lift="{ value }">
        <span class="jd-mono">{{ fmtNum(value) }}</span>
      </template>
      <template #cell:disabled_reason="{ value }">
        <span class="cell-muted">{{ value || '—' }}</span>
      </template>
      <template v-if="auth.isAdmin" #row-actions="{ row }">
        <template v-if="row.kind !== 'platform'">
          <button v-if="row.enabled" type="button" class="jd-btn jd-btn-danger jd-btn-sm" @click="ask(row, false)">Disable</button>
          <button v-else type="button" class="jd-btn jd-btn-success jd-btn-sm" @click="ask(row, true)">Enable</button>
        </template>
      </template>
    </DataTable>

    <Modal v-model="showToggle" :title="toggle.enable ? 'Enable source' : 'Disable source'" width="520px">
      <template v-if="toggle.enable">
        <p class="confirm-text">
          Re-enable {{ toggle.row?.name }}? Auto-disable will not fire before n ≥ {{ (toggle.row?.n_evaluated ?? 0) + (thresholds.min_n ?? 0) }}.
          Applies to news extracted from now on.
        </p>
      </template>
      <template v-else>
        <p class="confirm-text">
          A story whose every source is disabled is labelled ignore when it is extracted from now on (shadow scoring). Existing items keep their action.
        </p>
        <label class="jd-label" for="source-reason" style="margin-top:14px">Reason (required)</label>
        <textarea id="source-reason" v-model="toggle.reason" class="jd-input" rows="3" maxlength="300"></textarea>
      </template>
      <template #footer>
        <button type="button" class="jd-btn jd-btn-ghost jd-btn-sm" @click="showToggle = false">Cancel</button>
        <button type="button" class="jd-btn jd-btn-sm" :class="toggle.enable ? 'jd-btn-success' : 'jd-btn-danger'"
                :disabled="saving || (!toggle.enable && !toggle.reason.trim())" @click="confirmToggle">
          <i v-if="saving" class="pi pi-spin pi-spinner"></i> Confirm
        </button>
      </template>
    </Modal>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import DataTable from '@/components/common/DataTable.vue'
import Modal from '@/components/common/Modal.vue'
import { getNewsSources, setNewsSource } from '@/api/news'
import { useAuthStore } from '@/stores/auth'
import { useToast } from '@/composables/useToast'
import { detailOf } from '@/composables/useApiError'

const auth = useAuthStore()
const toast = useToast()

const sources = ref([])
const thresholds = ref({})
const loading = ref(false)
const error = ref(null)

const showToggle = ref(false)
const toggle = reactive({ row: null, enable: false, reason: '' })
const saving = ref(false)

const columns = [
  { key: 'name', header: 'Name', sortable: true },
  { key: 'kind', header: 'Kind', filterable: true, filterLabel: 'Kind' },
  { key: 'parent', header: 'Parent' },
  { key: 'state', header: 'State', accessor: (r) => (r.enabled ? 'on' : 'off'), filterable: true, filterLabel: 'State' },
  { key: 'score', header: 'Score', align: 'right' },
  { key: 'lift', header: 'Lift', align: 'right' },
  { key: 'n_evaluated', header: 'n', sortable: true, align: 'right' },
  { key: 'hits', header: 'Hits', align: 'right' },
  { key: 'disabled_reason', header: 'Disabled reason' },
]

const fmtNum = (v) => (v == null ? '—' : Number(v).toFixed(2))

async function load() {
  loading.value = true
  error.value = null
  try {
    const { data } = await getNewsSources()
    sources.value = data.sources
    thresholds.value = data.thresholds || {}
  } catch (e) {
    error.value = detailOf(e, 'Could not load news sources')
  } finally {
    loading.value = false
  }
}

function ask(row, enable) {
  toggle.row = row
  toggle.enable = enable
  toggle.reason = ''
  showToggle.value = true
}

async function confirmToggle() {
  saving.value = true
  try {
    const body = toggle.enable ? { enabled: true } : { enabled: false, reason: toggle.reason.trim() }
    await setNewsSource(toggle.row.id, body)
    toast.success(`${toggle.row.name} ${toggle.enable ? 'enabled' : 'disabled'}`)
    showToggle.value = false
    load()
  } catch (e) {
    toast.error(detailOf(e, 'Could not update the source'))
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.head-actions { display: flex; align-items: flex-end; gap: 10px; flex-wrap: wrap; }
.cell-muted { color: var(--jd-text-muted); font-size: 12px; }
.confirm-text { margin: 0; font-size: 13.5px; line-height: 1.5; color: var(--jd-text); }
</style>
