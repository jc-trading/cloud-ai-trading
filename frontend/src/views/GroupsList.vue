<template>
  <div>
    <div class="jd-page-head">
      <div>
        <h1 class="jd-h1">Strategy Groups</h1>
      </div>
      <div class="head-actions">
        <router-link v-if="auth.isAdmin" to="/groups/new" class="jd-btn jd-btn-primary jd-btn-sm">
          <i class="pi pi-plus"></i> New group
        </router-link>
        <button type="button" class="jd-btn jd-btn-ghost jd-btn-sm" :disabled="loading" @click="load">
          <i class="pi pi-refresh"></i> Refresh
        </button>
      </div>
    </div>

    <DataTable
      :columns="columns"
      :data="rows"
      row-key="id"
      :page-size="25"
      clickable-rows
      :loading="loading"
      :error="error"
      @row-click="(row) => router.push('/groups/' + row.id)"
    >
      <template #cell:name="{ value }">
        <span class="font-semibold">{{ value }}</span>
      </template>
      <template #cell:strategy_key="{ value }">
        <span class="jd-mono" style="font-size:12px">{{ value }}</span>
      </template>
      <template #cell:status="{ value }">
        <span :class="['jd-badge', statusColor(value)]">{{ value }}</span>
      </template>
      <template #cell:created_at="{ value }">
        <span class="jd-mono" style="color: var(--jd-text-muted)">{{ fmtTime(value) }}</span>
      </template>
      <template #empty>
        <div class="jd-empty"><i class="pi pi-sitemap"></i><p>No strategy groups yet.</p></div>
      </template>
    </DataTable>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import DataTable from '@/components/common/DataTable.vue'
import { listStrategyGroups, getStrategyGroup } from '@/api/strategyGroups'
import { useAuthStore } from '@/stores/auth'
import { detailOf } from '@/composables/useApiError'

const router = useRouter()
const auth = useAuthStore()

const rows = ref([])
const loading = ref(false)
const error = ref(null)

const columns = [
  { key: 'name', header: 'Name', sortable: true },
  { key: 'strategy_key', header: 'Strategy' },
  { key: 'status', header: 'Status', filterable: true, filterLabel: 'Status' },
  { key: 'screener', header: 'Screener' },
  { key: 'created_at', header: 'Created', sortable: true },
]

const statusColor = (s) => ({ active: 'green', paused: 'yellow', archived: 'gray' }[s] || 'gray')

const fmtTime = (iso) => {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

const screenerSummary = (detail) => {
  if (!detail) return '—'
  const s = detail.screener_json || {}
  if (Object.keys(s).length === 0) return 'legacy v1 pool'
  let text = `${(s.rules || []).length} rules`
  if (s.include?.length) text += ` · +${s.include.length}`
  if (s.exclude?.length) text += ` · −${s.exclude.length}`
  return text
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const { data } = await listStrategyGroups()
    const details = await Promise.all(
      data.map((g) => getStrategyGroup(g.id).then((r) => r.data).catch(() => null)))
    rows.value = data.map((g, i) => ({ ...g, screener: screenerSummary(details[i]) }))
  } catch (e) {
    error.value = detailOf(e, 'Failed to load strategy groups')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.head-actions { display: flex; align-items: flex-end; gap: 10px; flex-wrap: wrap; }
</style>
