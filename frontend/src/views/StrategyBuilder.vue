<template>
  <div class="jd-page">
    <!-- Header -->
    <div class="jd-section-header">
      <div>
        <h1 class="jd-section-title">Quant Strategy Builder</h1>
        <p class="jd-section-description">Create and backtest quantitative trading strategies</p>
      </div>
    </div>

    <!-- Strategy Tabs -->
    <div class="jd-card">
      <div class="jd-tabs">
        <button class="jd-tab" :class="{ active: tab === 'create' }" @click="tab = 'create'">Create New Strategy</button>
        <button class="jd-tab" :class="{ active: tab === 'strategies' }" @click="tab = 'strategies'">My Strategies</button>
        <button class="jd-tab" :class="{ active: tab === 'backtest' }" @click="tab = 'backtest'">Backtest Results</button>
      </div>

      <!-- Create New Strategy -->
      <div v-show="tab === 'create'" class="jd-card-body" style="display: flex; flex-direction: column; gap: 24px;">
        <!-- Strategy Name -->
        <div class="jd-form-group">
          <label class="jd-label">Strategy Name</label>
          <input v-model="strategyName" placeholder="e.g., MA Crossover Strategy" class="w-full jd-input" />
        </div>

        <p v-if="formError" style="color: var(--jd-red); font-size: 13px; margin: -12px 0 0;">{{ formError }}</p>

        <!-- Entry Conditions -->
        <div>
          <h3 style="font-size: 18px; font-weight: bold; color: var(--jd-text); margin-bottom: 16px;">Entry Conditions</h3>
          <div style="display: flex; flex-direction: column; gap: 12px; background: var(--jd-card); border: 1px solid var(--jd-border); border-radius: 8px; padding: 16px;">
            <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px;">
              <div class="jd-form-group">
                <label class="jd-label">Indicator 1</label>
                <select v-model="entryIndicator" class="w-full jd-input jd-select">
                  <option value="" disabled selected>Select indicator</option>
                  <option v-for="o in indicators" :key="o.value" :value="o.value">{{ o.label }}</option>
                </select>
              </div>
              <div class="jd-form-group">
                <label class="jd-label">Condition</label>
                <select v-model="entryCondition" class="w-full jd-input jd-select">
                  <option value="" disabled selected>Select condition</option>
                  <option v-for="o in conditions" :key="o.value" :value="o.value">{{ o.label }}</option>
                </select>
              </div>
              <div class="jd-form-group">
                <label class="jd-label">Value</label>
                <input v-model="entryValue" placeholder="Enter value" class="w-full jd-input" />
              </div>
            </div>
          </div>
        </div>

        <!-- Exit Conditions -->
        <div>
          <h3 style="font-size: 18px; font-weight: bold; color: var(--jd-text); margin-bottom: 16px;">Exit Conditions</h3>
          <div style="display: flex; flex-direction: column; gap: 12px; background: var(--jd-card); border: 1px solid var(--jd-border); border-radius: 8px; padding: 16px;">
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
              <div class="jd-form-group">
                <label class="jd-label">Take Profit (%)</label>
                <input v-model.number="takeProfit" type="number" placeholder="2.0" class="w-full jd-input" />
              </div>
              <div class="jd-form-group">
                <label class="jd-label">Stop Loss (%)</label>
                <input v-model.number="stopLoss" type="number" placeholder="1.0" class="w-full jd-input" />
              </div>
            </div>
          </div>
        </div>

        <!-- Risk Management -->
        <div>
          <h3 style="font-size: 18px; font-weight: bold; color: var(--jd-text); margin-bottom: 16px;">Risk Management</h3>
          <div style="display: flex; flex-direction: column; gap: 12px; background: var(--jd-card); border: 1px solid var(--jd-border); border-radius: 8px; padding: 16px;">
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
              <div class="jd-form-group">
                <label class="jd-label">Position Size (%)</label>
                <input v-model.number="positionSize" type="number" placeholder="5" class="w-full jd-input" />
              </div>
              <div class="jd-form-group">
                <label class="jd-label">Max Drawdown (%)</label>
                <input v-model.number="maxDrawdown" type="number" placeholder="10" class="w-full jd-input" />
              </div>
            </div>
          </div>
        </div>

        <!-- Action Buttons -->
        <div style="display: flex; gap: 16px; padding-top: 16px;">
          <button class="jd-btn jd-btn-primary" :disabled="saving" @click="saveStrategy">
            <i class="pi pi-save"></i> {{ saving ? 'Saving…' : 'Save Strategy' }}
          </button>
          <button class="jd-btn jd-btn-ghost" :disabled="true" title="Backtesting isn't available yet">
            <i class="pi pi-play"></i> Backtest
          </button>
        </div>
      </div>

      <!-- My Strategies -->
      <div v-show="tab === 'strategies'" class="jd-card-body">
        <DataTable
          :columns="strategyColumns"
          :data="strategies"
          row-key="id"
          :searchable="['name']"
          search-placeholder="Search strategies…"
          :page-size="10"
          :loading="loading"
          :error="listError"
          empty-text="No strategies created yet. Create one to get started."
        >
          <template #cell:status="{ row }">
            <span class="jd-badge" :class="row.is_active ? 'green' : 'gray'">
              {{ row.is_active ? 'Active' : 'Inactive' }}
            </span>
          </template>
          <template #cell:symbols="{ value }">{{ (value || []).join(', ') }}</template>
          <template #row-actions="{ row }">
            <div style="display: flex; gap: 8px;">
              <button
                class="jd-btn jd-btn-ghost jd-btn-sm"
                :disabled="busyId === row.id"
                @click="toggle(row)"
              >
                <i :class="row.is_active ? 'pi pi-pause' : 'pi pi-play'"></i>
                {{ row.is_active ? 'Deactivate' : 'Activate' }}
              </button>
              <button class="jd-btn jd-btn-ghost jd-btn-sm" :disabled="true" title="Backtesting isn't available yet">
                <i class="pi pi-chart-bar"></i> Backtest
              </button>
              <button
                class="jd-btn jd-btn-danger jd-btn-sm"
                :disabled="busyId === row.id"
                @click="remove(row)"
              >
                <i class="pi pi-trash"></i> Delete
              </button>
            </div>
          </template>
        </DataTable>
      </div>

      <!-- Backtest Results -->
      <div v-show="tab === 'backtest'" class="jd-card-body">
        <div style="width: 100%; min-height: 240px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 12px; text-align: center;">
          <i class="pi pi-chart-bar" style="font-size: 32px; color: var(--jd-text-muted);"></i>
          <p style="color: var(--jd-text); font-weight: bold;">Backtesting is not available yet.</p>
          <p style="color: var(--jd-text-muted); font-size: 13px;">There is no backtest endpoint on the server. This tab will show results once backtesting is supported.</p>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted } from 'vue'
import DataTable from '@/components/common/DataTable.vue'
import { listStrategies, createStrategy, deleteStrategy, toggleStrategy } from '@/api/strategy'
import { useToast } from '@/composables/useToast'

const toast = useToast()

const tab = ref('create')

// --- Create form state ---
const strategyName = ref('')
const entryIndicator = ref('')
const entryCondition = ref('')
const entryValue = ref('')
const takeProfit = ref(null)
const stopLoss = ref(null)
const positionSize = ref(null)
const maxDrawdown = ref(null)
const formError = ref('')
const saving = ref(false)

const indicators = ref([
  { label: 'Moving Average', value: 'ma' },
  { label: 'RSI', value: 'rsi' },
  { label: 'MACD', value: 'macd' },
  { label: 'Bollinger Bands', value: 'bb' }
])

const conditions = ref([
  { label: 'Greater Than', value: 'gt' },
  { label: 'Less Than', value: 'lt' },
  { label: 'Equals', value: 'eq' },
  { label: 'Crosses Above', value: 'crossover' },
  { label: 'Crosses Below', value: 'crossunder' }
])

// --- My Strategies state ---
const strategies = ref([])
const loading = ref(false)
const listError = ref(null)
const busyId = ref(null)

const strategyColumns = [
  { key: 'name', header: 'Strategy Name', sortable: true },
  { key: 'status', header: 'Status', accessor: (r) => (r.is_active ? 'Active' : 'Inactive'), filterable: true, filterLabel: 'Status' },
  { key: 'timeframe', header: 'Timeframe', sortable: true },
  { key: 'symbols', header: 'Symbols' },
]

const resetForm = () => {
  strategyName.value = ''
  entryIndicator.value = ''
  entryCondition.value = ''
  entryValue.value = ''
  takeProfit.value = null
  stopLoss.value = null
  positionSize.value = null
  maxDrawdown.value = null
  formError.value = ''
}

async function loadStrategies() {
  loading.value = true
  listError.value = null
  try {
    const res = await listStrategies()
    strategies.value = Array.isArray(res.data) ? res.data : []
  } catch (err) {
    listError.value = err.response?.data?.detail || 'Failed to load strategies. Check that the API is up.'
  } finally {
    loading.value = false
  }
}

async function saveStrategy() {
  const name = strategyName.value.trim()
  formError.value = ''
  if (!name) {
    formError.value = 'Strategy name is required.'
    return
  }

  // Entry conditions — include only the fields the user actually set.
  const entry_conditions = {}
  if (entryIndicator.value) entry_conditions.indicator = entryIndicator.value
  if (entryCondition.value) entry_conditions.condition = entryCondition.value
  if (entryValue.value !== '' && entryValue.value != null) entry_conditions.value = entryValue.value

  // Exit + risk knobs persisted under indicators_config.risk (no dedicated server field).
  const risk = {}
  if (takeProfit.value != null && takeProfit.value !== '') risk.take_profit = takeProfit.value
  if (stopLoss.value != null && stopLoss.value !== '') risk.stop_loss = stopLoss.value
  if (positionSize.value != null && positionSize.value !== '') risk.position_size = positionSize.value
  if (maxDrawdown.value != null && maxDrawdown.value !== '') risk.max_drawdown = maxDrawdown.value

  const payload = {
    name,
    symbols: ['BTC/USDT'],
    timeframe: '1h',
    entry_conditions,
    indicators_config: Object.keys(risk).length ? { risk } : {},
  }

  saving.value = true
  try {
    await createStrategy(payload)
    toast.add({ severity: 'success', summary: 'Saved', detail: name })
    resetForm()
    tab.value = 'strategies'
    await loadStrategies()
  } catch (err) {
    formError.value = err.response?.data?.detail || 'Failed to save strategy.'
  } finally {
    saving.value = false
  }
}

async function remove(row) {
  busyId.value = row.id
  try {
    await deleteStrategy(row.id)
    toast.add({ severity: 'success', summary: 'Deleted', detail: row.name })
    await loadStrategies()
  } catch (err) {
    toast.add({ severity: 'error', summary: 'Delete failed', detail: err.response?.data?.detail || 'Request failed' })
  } finally {
    busyId.value = null
  }
}

async function toggle(row) {
  busyId.value = row.id
  try {
    await toggleStrategy(row.id)
    toast.add({
      severity: 'success',
      summary: row.is_active ? 'Deactivated' : 'Activated',
      detail: row.name,
    })
    await loadStrategies()
  } catch (err) {
    toast.add({ severity: 'error', summary: 'Toggle failed', detail: err.response?.data?.detail || 'Request failed' })
  } finally {
    busyId.value = null
  }
}

// Load on mount and whenever the user switches to My Strategies.
onMounted(loadStrategies)
watch(tab, (t) => { if (t === 'strategies') loadStrategies() })
</script>

<style scoped>
.w-full {
  width: 100%;
}
</style>
