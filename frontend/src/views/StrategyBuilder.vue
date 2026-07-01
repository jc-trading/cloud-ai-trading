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

        <!-- Entry Conditions -->
        <div>
          <h3 style="font-size: 18px; font-weight: bold; color: var(--jd-text); margin-bottom: 16px;">Entry Conditions</h3>
          <div style="display: flex; flex-direction: column; gap: 12px; background: var(--jd-card); border: 1px solid var(--jd-border); border-radius: 8px; padding: 16px;">
            <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px;">
              <div class="jd-form-group">
                <label class="jd-label">Indicator 1</label>
                <select class="w-full jd-input jd-select">
                  <option value="" disabled selected>Select indicator</option>
                  <option v-for="o in indicators" :key="o.value" :value="o.value">{{ o.label }}</option>
                </select>
              </div>
              <div class="jd-form-group">
                <label class="jd-label">Condition</label>
                <select class="w-full jd-input jd-select">
                  <option value="" disabled selected>Select condition</option>
                  <option v-for="o in conditions" :key="o.value" :value="o.value">{{ o.label }}</option>
                </select>
              </div>
              <div class="jd-form-group">
                <label class="jd-label">Value</label>
                <input placeholder="Enter value" class="w-full jd-input" />
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
                <input type="number" placeholder="2.0" class="w-full jd-input" />
              </div>
              <div class="jd-form-group">
                <label class="jd-label">Stop Loss (%)</label>
                <input type="number" placeholder="1.0" class="w-full jd-input" />
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
                <input type="number" placeholder="5" class="w-full jd-input" />
              </div>
              <div class="jd-form-group">
                <label class="jd-label">Max Drawdown (%)</label>
                <input type="number" placeholder="10" class="w-full jd-input" />
              </div>
            </div>
          </div>
        </div>

        <!-- Action Buttons -->
        <div style="display: flex; gap: 16px; padding-top: 16px;">
          <button class="jd-btn jd-btn-primary"><i class="pi pi-save"></i> Save Strategy</button>
          <button class="jd-btn jd-btn-ghost"><i class="pi pi-play"></i> Backtest</button>
        </div>
      </div>

      <!-- My Strategies -->
      <div v-show="tab === 'strategies'" class="jd-card-body">
        <DataTable
          :columns="strategyColumns"
          :data="strategies"
          row-key="name"
          :searchable="['name']"
          search-placeholder="Search strategies…"
          :page-size="10"
          empty-text="No strategies created yet. Create one to get started."
        >
          <template #cell:status="{ value }">
            <span class="jd-badge" :class="value === 'Active' ? 'green' : 'yellow'">{{ value }}</span>
          </template>
          <template #cell:winRate="{ value }">{{ value }}%</template>
          <template #row-actions>
            <div style="display: flex; gap: 8px;">
              <button class="jd-btn jd-btn-ghost jd-btn-sm"><i class="pi pi-pencil"></i> Edit</button>
              <button class="jd-btn jd-btn-ghost jd-btn-sm"><i class="pi pi-chart-bar"></i> Backtest</button>
              <button class="jd-btn jd-btn-danger jd-btn-sm"><i class="pi pi-trash"></i> Delete</button>
            </div>
          </template>
        </DataTable>
      </div>

      <!-- Backtest Results -->
      <div v-show="tab === 'backtest'" class="jd-card-body">
        <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px;">
          <div class="jd-stat-card blue">
            <div class="jd-stat-icon blue"></div>
            <p class="jd-stat-label">Total Return</p>
            <p class="jd-stat-value" style="color: var(--jd-green);">--</p>
          </div>
          <div class="jd-stat-card purple">
            <div class="jd-stat-icon purple"></div>
            <p class="jd-stat-label">Sharpe Ratio</p>
            <p class="jd-stat-value">--</p>
          </div>
          <div class="jd-stat-card red">
            <div class="jd-stat-icon red"></div>
            <p class="jd-stat-label">Max Drawdown</p>
            <p class="jd-stat-value">--</p>
          </div>
          <div class="jd-stat-card cyan">
            <div class="jd-stat-icon cyan"></div>
            <p class="jd-stat-label">Win Rate</p>
            <p class="jd-stat-value">--</p>
          </div>
        </div>

        <div class="jd-card">
          <div class="jd-card-header">
            <h3 class="jd-card-title">Performance Chart</h3>
          </div>
          <div style="width: 100%; height: 384px; display: flex; align-items: center; justify-content: center; background: var(--jd-card); border-radius: 8px;">
            <p style="color: var(--jd-text-muted);">Backtest results will be displayed here</p>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import DataTable from '@/components/common/DataTable.vue'

const tab = ref('create')

const strategyName = ref('')

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

const strategies = ref([])

const strategyColumns = [
  { key: 'name', header: 'Strategy Name', sortable: true },
  { key: 'status', header: 'Status', filterable: true, filterLabel: 'Status' },
  { key: 'winRate', header: 'Win Rate', sortable: true, align: 'right' },
  { key: 'totalTrades', header: 'Total Trades', sortable: true, align: 'right' },
]
</script>

<style scoped>
.w-full {
  width: 100%;
}
</style>
