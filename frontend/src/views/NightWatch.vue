<template>
  <div class="jd-page">
    <!-- Header -->
    <div>
      <h1 class="text-3xl font-bold mb-1">深夜股票检测 <span style="color: var(--jd-text-muted); font-size: 0.6em; font-weight: 400">Night Watch</span></h1>
      <p class="text-sm" style="color: var(--jd-text-muted)">
        The G2 paper-observation run, one row per US session — signal output, system-account orders, LLM cost, equity.
        <span style="color: var(--jd-text-faint)">Read-only view over what the cycles already record.</span>
      </p>
    </div>

    <!-- Safety banner -->
    <div v-if="safetyActive" class="jd-card" style="border-color: var(--jd-red)">
      <div class="jd-card-body" style="color: var(--jd-red)">
        <i class="pi pi-exclamation-triangle"></i>
        <strong> Protections active:</strong>
        <span v-if="safety.halted"> HALTED{{ safety.halted_until ? ` until ${safety.halted_until}` : '' }}</span>
        <span v-if="safety.paused_until"> · paused until {{ safety.paused_until }}</span>
        <span v-if="safety.reason" style="color: var(--jd-text-muted)"> — {{ safety.reason }}</span>
      </div>
    </div>

    <!-- Now panel: cycle heartbeats -->
    <div class="jd-card">
      <div class="jd-card-header flex-header">
        <h2 class="jd-card-title">Cycles — last beat</h2>
        <div class="header-right">
          <span class="update-text">{{ loading ? 'Loading…' : `${nights.length} nights` }}</span>
          <button class="jd-btn jd-btn-ghost jd-btn-sm" :disabled="loading" @click="load">
            <i class="pi pi-refresh" :class="{ 'animate-spin': loading }"></i>
          </button>
        </div>
      </div>
      <div class="jd-card-body" style="display:flex; flex-wrap:wrap; gap:12px;">
        <div v-for="h in heartbeats" :key="h.name" class="jd-badge"
             :class="beatClass(h)" style="padding:8px 12px; font-size:13px;">
          <span class="font-mono">{{ h.name }}</span>
          <span style="color: var(--jd-text-muted)"> — {{ beatAge(h) }}</span>
        </div>
        <div v-if="!heartbeats.length && !loading" style="color: var(--jd-text-muted)">
          No heartbeats yet — system has not run.
        </div>
      </div>
    </div>

    <!-- Per-night table -->
    <div class="jd-card">
      <div class="jd-card-header"><h2 class="jd-card-title">Nights</h2></div>
      <div class="jd-card-body">
        <DataTable
          :columns="columns"
          :data="nights"
          row-key="date"
          :searchable="['date', 'shortlistText']"
          search-placeholder="Search date / symbol…"
          :page-size="14"
          :loading="loading"
          :error="error"
        >
          <template #cell:date="{ value }">
            <span class="font-mono font-semibold">{{ value }}</span>
          </template>
          <template #cell:ranAt="{ value }">
            <span v-if="value" class="font-mono" style="color: var(--jd-text-muted)">{{ fmtTime(value) }}</span>
            <span v-else class="jd-badge" style="opacity:.7">no signal</span>
          </template>
          <template #cell:recs="{ row }">
            <span class="font-mono">{{ row.signal.recommendations }}</span>
            <span v-if="row.signal.explained" style="color: var(--jd-text-faint); font-size:11px">
              · {{ row.signal.explained }} explained</span>
          </template>
          <template #cell:shortlistText="{ value }">
            <span v-if="value" class="font-mono" style="font-size:12px">{{ value }}</span>
            <span v-else style="color: var(--jd-text-faint)">—</span>
          </template>
          <template #cell:entries_filled="{ value }">
            <span :class="value ? 'jd-badge green' : ''" class="font-mono">{{ value }}</span>
          </template>
          <template #cell:exits_filled="{ value }">
            <span :class="value ? 'jd-badge yellow' : ''" class="font-mono">{{ value }}</span>
          </template>
          <template #cell:orders_rejected="{ value }">
            <span :class="value ? 'jd-badge red' : ''" class="font-mono">{{ value }}</span>
          </template>
          <template #cell:llmText="{ row }">
            <span v-if="row.llm.calls" class="font-mono" style="font-size:12px">
              {{ row.llm.calls }} · ${{ row.llm.cost_usd.toFixed(4) }}</span>
            <span v-else style="color: var(--jd-text-faint)">—</span>
          </template>
          <template #cell:equity="{ row }">
            <span v-if="row.equity != null" class="font-mono">
              ${{ row.equity.toFixed(2) }}
              <span v-if="row.open_positions" style="color: var(--jd-text-muted); font-size:11px">
                · {{ row.open_positions }} pos</span>
            </span>
            <span v-else style="color: var(--jd-text-faint)">—</span>
          </template>
          <template #empty>
            <div class="jd-empty">
              <i class="pi pi-moon"></i>
              <p>No nights recorded yet — start the run with <code>./scripts/night-watch.sh start</code>.</p>
            </div>
          </template>
        </DataTable>
      </div>
    </div>

    <!-- System-account orders, flattened -->
    <div class="jd-card">
      <div class="jd-card-header"><h2 class="jd-card-title">System-account orders (对照账户)</h2></div>
      <div class="jd-card-body">
        <DataTable
          :columns="orderColumns"
          :data="orders"
          row-key="id"
          :searchable="['symbol', 'reason', 'status']"
          search-placeholder="Search symbol / reason…"
          :page-size="10"
          :loading="loading"
          :error="error"
        >
          <template #cell:requested_at="{ value }">
            <span class="font-mono" style="color: var(--jd-text-muted)">{{ fmtTime(value) }}</span>
          </template>
          <template #cell:symbol="{ value }">
            <span class="font-semibold">{{ value }}</span>
          </template>
          <template #cell:side="{ value }">
            <span class="jd-badge" :class="value === 'buy' ? 'green' : 'red'">{{ value }}</span>
          </template>
          <template #cell:status="{ row }">
            <span class="jd-badge" :class="row.status === 'filled' ? 'green' : 'red'"
                  :title="row.reject_reason || ''">{{ row.status }}</span>
          </template>
          <template #cell:price="{ value }">
            <span v-if="value != null" class="font-mono">${{ value.toFixed(2) }}</span>
            <span v-else style="color: var(--jd-text-faint)">—</span>
          </template>
          <template #empty>
            <div class="jd-empty">
              <i class="pi pi-inbox"></i>
              <p>No orders yet — the system enters only shortlisted names during RTH.</p>
            </div>
          </template>
        </DataTable>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import DataTable from '@/components/common/DataTable.vue'
import { getNightWatchLog } from '@/api/nightwatch'

const nightsRaw = ref([])
const heartbeats = ref([])
const safety = ref(null)
const loading = ref(false)
const error = ref(null)

const safetyActive = computed(() =>
  safety.value && (safety.value.halted || safety.value.paused_until))

const nights = computed(() => nightsRaw.value.map((n) => ({
  ...n,
  ranAt: n.signal.ran_at,
  recs: n.signal.recommendations,
  shortlistText: n.signal.shortlist
    .map((s) => `${s.symbol}#${s.rank} ${s.confidence != null ? s.confidence.toFixed(1) : ''}`)
    .join(' · '),
  llmText: n.llm.calls,
})))

const orders = computed(() =>
  nightsRaw.value.flatMap((n) => n.orders)
    .sort((a, b) => b.requested_at.localeCompare(a.requested_at)))

const columns = [
  { key: 'date', header: 'Session', sortable: true },
  { key: 'ranAt', header: 'Signal ran', sortable: true },
  { key: 'recs', header: 'Recs', sortable: true, align: 'right' },
  { key: 'shortlistText', header: 'Shortlist' },
  { key: 'entries_filled', header: 'Entries', sortable: true, align: 'center' },
  { key: 'exits_filled', header: 'Exits', sortable: true, align: 'center' },
  { key: 'orders_rejected', header: 'Rejected', sortable: true, align: 'center' },
  { key: 'llmText', header: 'LLM (calls · $)', align: 'right' },
  { key: 'equity', header: 'EOD equity', sortable: true, align: 'right' },
]

const orderColumns = [
  { key: 'requested_at', header: 'Time', sortable: true },
  { key: 'symbol', header: 'Symbol', sortable: true },
  { key: 'side', header: 'Side', align: 'center',
    filterable: true, filterLabel: 'Side' },
  { key: 'reason', header: 'Reason', sortable: true,
    filterable: true, filterLabel: 'Reason' },
  { key: 'qty', header: 'Qty', align: 'right' },
  { key: 'price', header: 'Fill', align: 'right' },
  { key: 'status', header: 'Status', align: 'center',
    filterable: true, filterLabel: 'Status' },
]

const fmtTime = (iso) => {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

const beatAge = (h) => {
  if (!h.last_beat_at) return 'never'
  const mins = Math.floor((Date.now() - new Date(h.last_beat_at).getTime()) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  if (mins < 48 * 60) return `${Math.floor(mins / 60)}h ago`
  return `${Math.floor(mins / 1440)}d ago`
}

const beatClass = (h) => {
  if (!h.last_beat_at) return ''
  const mins = (Date.now() - new Date(h.last_beat_at).getTime()) / 60000
  if (mins < 5) return 'green'
  if (mins < 26 * 60) return 'yellow'
  return ''
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const { data } = await getNightWatchLog(30)
    nightsRaw.value = data.nights || []
    heartbeats.value = data.now?.heartbeats || []
    safety.value = data.now?.safety || null
  } catch (e) {
    error.value = e?.response?.data?.detail || 'Failed to load night-watch log'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
