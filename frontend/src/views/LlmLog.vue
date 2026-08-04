<template>
  <div class="jd-page">
    <!-- Header -->
    <div>
      <h1 class="text-3xl font-bold mb-1">LLM Log</h1>
      <p class="text-sm" style="color: var(--jd-text-muted)">
        Every LLM call the system makes — platform, model, tokens, unit price, cost.
        <span style="color: var(--jd-text-faint)">v3 uses the LLM for recommendation explanations only (no trading decisions).</span>
      </p>
    </div>

    <!-- Summary tiles -->
    <div class="stats-grid">
      <div class="jd-stat-card" style="--accent: var(--jd-cyan)">
        <div class="jd-stat-label">Total calls</div>
        <div class="jd-stat-value">{{ allTime.calls }}</div>
      </div>
      <div class="jd-stat-card" style="--accent: var(--jd-blue)">
        <div class="jd-stat-label">Total tokens (in / out)</div>
        <div class="jd-stat-value">{{ fmtInt(allTime.input_tokens) }} / {{ fmtInt(allTime.output_tokens) }}</div>
      </div>
      <div class="jd-stat-card" style="--accent: var(--jd-green)">
        <div class="jd-stat-label">Total cost (USD)</div>
        <div class="jd-stat-value font-mono">${{ fmtUsd(allTime.cost_usd) }}</div>
      </div>
      <div class="jd-stat-card" style="--accent: var(--jd-purple)">
        <div class="jd-stat-label">Today (calls · USD)</div>
        <div class="jd-stat-value">
          {{ today.calls }}
          <span style="color: var(--jd-text-muted)"> · </span>
          <span class="font-mono">${{ fmtUsd(today.cost_usd) }}</span>
        </div>
      </div>
    </div>

    <!-- Per-model breakdown -->
    <div v-if="byModel.length" class="jd-card">
      <div class="jd-card-header"><h2 class="jd-card-title">By model</h2></div>
      <div class="jd-card-body" style="display:flex; flex-wrap:wrap; gap:12px;">
        <div v-for="m in byModel" :key="m.model" class="jd-badge blue"
             style="padding:8px 12px; font-size:13px;">
          <span class="font-mono">{{ m.model }}</span>
          <span style="color: var(--jd-text-muted)"> — {{ m.calls }} calls · </span>
          <span class="font-mono">${{ fmtUsd(m.cost_usd) }}</span>
        </div>
      </div>
    </div>

    <!-- Call log table -->
    <div class="jd-card">
      <div class="jd-card-header flex-header">
        <h2 class="jd-card-title">Call log</h2>
        <div class="header-right">
          <span class="update-text">{{ loading ? 'Loading…' : `${items.length} rows` }}</span>
          <button class="jd-btn jd-btn-ghost jd-btn-sm" :disabled="loading" @click="load">
            <i class="pi pi-refresh" :class="{ 'animate-spin': loading }"></i>
          </button>
        </div>
      </div>
      <div class="jd-card-body">
        <DataTable
          :columns="columns"
          :data="items"
          row-key="id"
          :searchable="['symbol', 'model', 'context']"
          search-placeholder="Search symbol / model / context…"
          :page-size="25"
          :loading="loading"
          :error="error"
        >
          <template #cell:created_at="{ value }">
            <span class="font-mono" style="color: var(--jd-text-muted)">{{ fmtTime(value) }}</span>
          </template>
          <template #cell:symbol="{ value }">
            <span class="font-semibold">{{ value || '—' }}</span>
          </template>
          <template #cell:model="{ value }">
            <span class="font-mono" style="font-size:12px">{{ value }}</span>
          </template>
          <template #cell:input_tokens="{ value }">
            <span class="font-mono">{{ fmtInt(value) }}</span>
          </template>
          <template #cell:output_tokens="{ value }">
            <span class="font-mono">{{ fmtInt(value) }}</span>
          </template>
          <template #cell:cost_usd="{ value }">
            <span class="font-mono price-up">${{ fmtUsd(value) }}</span>
          </template>
          <template #cell:latency_ms="{ value }">
            <span style="color: var(--jd-text-muted)">{{ value != null ? value + ' ms' : '—' }}</span>
          </template>
          <template #cell:success="{ row }">
            <span v-if="row.success" class="jd-badge green">ok</span>
            <span v-else class="jd-badge red" :title="row.error || ''">error</span>
          </template>
          <template #empty>
            <div class="jd-empty">
              <i class="pi pi-bolt"></i>
              <p>No LLM calls yet — the explanation layer runs after the nightly signal cycle.</p>
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
import { getLlmLog } from '@/api/llm'

const items = ref([])
const summary = ref({ all_time: {}, today: {}, by_model: [], by_day: [] })
const loading = ref(false)
const error = ref(null)

const allTime = computed(() => ({
  calls: summary.value.all_time?.calls ?? 0,
  input_tokens: summary.value.all_time?.input_tokens ?? 0,
  output_tokens: summary.value.all_time?.output_tokens ?? 0,
  cost_usd: summary.value.all_time?.cost_usd ?? 0,
}))
const today = computed(() => ({
  calls: summary.value.today?.calls ?? 0,
  cost_usd: summary.value.today?.cost_usd ?? 0,
}))
const byModel = computed(() => summary.value.by_model ?? [])

const columns = [
  { key: 'created_at', header: 'Time', sortable: true },
  { key: 'context', header: 'Context', sortable: true,
    filterable: true, filterLabel: 'Context' },
  { key: 'symbol', header: 'Symbol', sortable: true },
  { key: 'model', header: 'Model', sortable: true,
    filterable: true, filterLabel: 'Model' },
  { key: 'input_tokens', header: 'In', sortable: true, align: 'right' },
  { key: 'output_tokens', header: 'Out', sortable: true, align: 'right' },
  { key: 'cost_usd', header: 'Cost', sortable: true, align: 'right' },
  { key: 'latency_ms', header: 'Latency', sortable: true, align: 'right' },
  { key: 'success', header: 'Status', align: 'center',
    filterable: true, filterLabel: 'Status' },
]

const fmtInt = (v) => (v ?? 0).toLocaleString()
const fmtUsd = (v) => Number(v ?? 0).toFixed(6)
const fmtTime = (iso) => {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleString(undefined, { month: 'short', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const { data } = await getLlmLog(500)
    summary.value = data.summary || { all_time: {}, today: {}, by_model: [], by_day: [] }
    items.value = data.items || []
  } catch (e) {
    error.value = e?.response?.data?.detail || 'Failed to load LLM log'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
