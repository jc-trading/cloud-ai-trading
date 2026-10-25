<template>
  <div class="jd-page">
    <div class="jd-page-head" style="margin-bottom:0">
      <div>
        <h1 class="jd-h1">Intraday watch</h1>
        <p v-if="data" class="jd-sub">
          Session {{ data.session }} · {{ data.entries.length }} / cap {{ data.cap }} (effective {{ data.cap_effective }})
        </p>
        <p class="jd-sub">subscribed / rejected come from the stream's last heartbeat.</p>
      </div>
      <div class="head-actions">
        <button type="button" class="jd-btn jd-btn-ghost jd-btn-sm" :disabled="loading || !auth.isAdmin" @click="load">
          <i class="pi pi-refresh" :class="{ 'pi-spin': loading }"></i> Refresh
        </button>
      </div>
    </div>

    <div v-if="!auth.isAdmin" class="jd-alert warning">
      <i class="pi pi-lock"></i> Admins only — the watch set lists group positions and owner watchlists.
    </div>
    <div v-else-if="error" class="jd-alert error" role="alert">
      <i class="pi pi-exclamation-triangle"></i> {{ error }}
    </div>
    <template v-else-if="data">
      <section class="jd-card">
        <div class="jd-card-header"><h2 class="jd-card-title">Stream</h2></div>
        <div class="jd-card-body">
          <div class="chips">
            <span v-if="data.stream.connected === true" class="jd-badge green">connected</span>
            <span v-else-if="data.stream.connected === false" class="jd-badge red">disconnected</span>
            <span v-else class="jd-badge gray">unknown</span>
            <span :class="['jd-badge', streamAgeColor(data.stream.age_s)]">age {{ data.stream.age_s ?? '—' }} s</span>
            <span class="jd-badge gray">period {{ data.stream.period_key ?? '—' }}</span>
            <span :class="['jd-badge', data.stream.errors.length > 0 ? 'red' : 'gray']">errors {{ data.stream.errors.length }}</span>
          </div>
          <div v-if="data.stream.errors.length" class="stream-errors">
            <div v-for="(e, i) in data.stream.errors" :key="i" class="jd-mono">{{ e.code }}: {{ e.msg }}</div>
          </div>
        </div>
      </section>

      <DataTable
        :columns="columns"
        :data="data.entries"
        row-key="symbol"
        :searchable="['symbol']"
        search-placeholder="Search symbol…"
        :page-size="50"
        :loading="loading"
        empty-text="Nothing on the watch set"
      >
        <template #cell:symbol="{ value }">
          <span class="font-semibold">{{ value }}</span>
        </template>
        <template #cell:tier="{ value }">
          <span :class="['jd-badge', tierColor(value)]">{{ value }}</span>
        </template>
        <template #cell:subscribed="{ value }">
          <span v-if="value === true" class="ok">✓</span>
          <span v-else-if="value === false" class="bad">✗</span>
          <span v-else class="cell-muted">—</span>
        </template>
        <template #cell:rejected="{ value }">
          <span v-if="value" class="bad">✗</span>
          <span v-else></span>
        </template>
      </DataTable>

      <section class="jd-card">
        <div class="jd-card-header"><h2 class="jd-card-title">Dropped (cap)</h2></div>
        <div class="jd-card-body">
          <div v-if="data.dropped.length" class="jd-table-wrap">
            <table class="jd-table">
              <thead>
                <tr><th>Symbol</th><th>Tier</th><th>Groups</th><th>Reason</th></tr>
              </thead>
              <tbody>
                <tr v-for="r in data.dropped" :key="r.symbol">
                  <td>{{ r.symbol }}</td>
                  <td><span :class="['jd-badge', tierColor(r.tier)]">{{ r.tier }}</span></td>
                  <td>{{ r.groups.join(', ') }}</td>
                  <td>{{ r.reason }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="d-nodata">Nothing dropped.</p>
        </div>
      </section>
    </template>
    <div v-else-if="loading" class="jd-empty"><p>Loading…</p></div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import DataTable from '@/components/common/DataTable.vue'
import { getWatch } from '@/api/intraday'
import { useAuthStore } from '@/stores/auth'
import { detailOf } from '@/composables/useApiError'
import { tierColor, streamAgeColor } from '@/composables/useIntraday'

const auth = useAuthStore()

const data = ref(null)
const loading = ref(false)
const error = ref(null)

const columns = [
  { key: 'symbol', header: 'Symbol', sortable: true },
  { key: 'tier', header: 'Tier', filterable: true, filterLabel: 'Tier' },
  { key: 'groups', header: 'Groups', accessor: (r) => r.groups.join(', ') },
  { key: 'reason', header: 'Reason' },
  { key: 'subscribed', header: 'Subscribed', align: 'center' },
  { key: 'rejected', header: 'Rejected', align: 'center' },
]

async function load() {
  if (!auth.isAdmin) return
  loading.value = true
  error.value = null
  try {
    const res = await getWatch()
    data.value = res.data
  } catch (e) {
    error.value = detailOf(e, 'Failed to load the watch set')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.head-actions { display: flex; align-items: flex-end; gap: 10px; flex-wrap: wrap; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; }
.stream-errors { margin-top: 12px; display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--jd-red); }
.cell-muted { color: var(--jd-text-muted); font-size: 12px; }
.ok { color: var(--jd-green); }
.bad { color: var(--jd-red); }
</style>
