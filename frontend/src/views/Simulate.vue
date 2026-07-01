<template>
  <div class="jd-page">
    <!-- Header -->
    <div class="jd-section-header">
      <div>
        <h1 class="jd-section-title">Paper Trading Simulator</h1>
        <p class="jd-section-description">Practice trading with virtual capital in a risk-free environment</p>
      </div>
    </div>

    <!-- Simulator Controls -->
    <div class="jd-card">
      <div class="jd-card-body" style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; align-items: flex-end;">
        <div class="jd-form-group">
          <label class="jd-label">Simulation Mode</label>
          <Dropdown
            v-model="simulationMode"
            :options="modes"
            optionLabel="label"
            optionValue="value"
            class="w-full jd-select"
          />
        </div>
        <div class="jd-form-group">
          <label class="jd-label">Start Date</label>
          <Calendar v-model="startDate" showIcon class="w-full jd-input"></Calendar>
        </div>
        <div class="jd-form-group">
          <label class="jd-label">End Date</label>
          <Calendar v-model="endDate" showIcon class="w-full jd-input"></Calendar>
        </div>
        <div>
          <Button label="Start Simulation" icon="pi pi-play" class="jd-btn jd-btn-primary"></Button>
        </div>
      </div>
    </div>

    <!-- Simulator Stats -->
    <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px;">
      <div class="jd-stat-card blue">
        <div class="jd-stat-icon blue"></div>
        <p class="jd-stat-label">Virtual Balance</p>
        <p class="jd-stat-value">$10,000.00</p>
      </div>
      <div class="jd-stat-card green">
        <div class="jd-stat-icon green"></div>
        <p class="jd-stat-label">Current Value</p>
        <p class="jd-stat-value">$10,000.00</p>
      </div>
      <div class="jd-stat-card cyan">
        <div class="jd-stat-icon cyan"></div>
        <p class="jd-stat-label">P/L</p>
        <p class="jd-stat-value">+$0.00</p>
      </div>
      <div class="jd-stat-card purple">
        <div class="jd-stat-icon purple"></div>
        <p class="jd-stat-label">Return %</p>
        <p class="jd-stat-value">0.00%</p>
      </div>
    </div>

    <!-- Simulator Trading Panel -->
    <div style="display: grid; grid-template-columns: 2fr 1fr; gap: 20px;">
      <!-- Order Entry -->
      <div class="jd-card">
        <div class="jd-card-header">
          <h2 class="jd-card-title">Simulator Order Entry</h2>
        </div>
        <div class="jd-card-body">
          <div style="display: flex; flex-direction: column; gap: 16px;">
            <!-- Symbol Selection -->
            <div class="jd-form-group">
              <label class="jd-label">Symbol</label>
              <InputText placeholder="BTCUSDT" class="w-full jd-input" />
            </div>

            <!-- Order Type and Side -->
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
              <div class="jd-form-group">
                <label class="jd-label">Order Type</label>
                <Dropdown
                  :options="orderTypes"
                  optionLabel="label"
                  optionValue="value"
                  class="w-full jd-select"
                />
              </div>
              <div class="jd-form-group">
                <label class="jd-label">Side</label>
                <Dropdown
                  :options="orderSides"
                  optionLabel="label"
                  optionValue="value"
                  class="w-full jd-select"
                />
              </div>
            </div>

            <!-- Price and Amount -->
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
              <div class="jd-form-group">
                <label class="jd-label">Price (USDT)</label>
                <InputNumber placeholder="0.00" class="w-full jd-input" />
              </div>
              <div class="jd-form-group">
                <label class="jd-label">Amount</label>
                <InputNumber placeholder="0.00" class="w-full jd-input" />
              </div>
            </div>

            <!-- Total -->
            <div style="background: var(--jd-card); border: 1px solid var(--jd-border); border-radius: 8px; padding: 12px; display: flex; justify-content: space-between; align-items: center;">
              <span style="color: var(--jd-text-muted);">Total (USDT)</span>
              <span style="font-weight: bold; color: var(--jd-text);">0.00</span>
            </div>

            <!-- Action Buttons -->
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px; padding-top: 16px;">
              <Button label="Buy" icon="pi pi-arrow-up" class="jd-btn jd-btn-success jd-btn-lg"></Button>
              <Button label="Sell" icon="pi pi-arrow-down" class="jd-btn jd-btn-danger jd-btn-lg"></Button>
            </div>
          </div>
        </div>
      </div>

      <!-- Portfolio Info -->
      <div class="jd-card">
        <div class="jd-card-header">
          <h3 class="jd-card-title">Portfolio</h3>
        </div>
        <div class="jd-card-body">
          <div style="display: flex; flex-direction: column; gap: 16px;">
            <div>
              <p style="color: var(--jd-text-muted); font-size: 12px; margin-bottom: 4px;">Cash</p>
              <p style="font-size: 24px; font-weight: bold; color: var(--jd-text);">$10,000.00</p>
            </div>
            <div style="border-top: 1px solid var(--jd-border); padding-top: 16px;">
              <p style="color: var(--jd-text-muted); font-size: 12px; margin-bottom: 4px;">Positions</p>
              <p style="font-size: 20px; font-weight: bold; color: var(--jd-text);">0</p>
            </div>
            <div style="border-top: 1px solid var(--jd-border); padding-top: 16px;">
              <p style="color: var(--jd-text-muted); font-size: 12px; margin-bottom: 4px;">Total Trades</p>
              <p style="font-size: 20px; font-weight: bold; color: var(--jd-text);">0</p>
            </div>
            <div style="border-top: 1px solid var(--jd-border); padding-top: 16px;">
              <p style="color: var(--jd-text-muted); font-size: 12px; margin-bottom: 4px;">Win Rate</p>
              <p style="font-size: 20px; font-weight: bold; color: var(--jd-text);">--</p>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Open Positions -->
    <DataTable
      :columns="positionColumns"
      :data="openPositions"
      :row-key="(row) => row.symbol"
      :searchable="['symbol']"
      search-placeholder="Search positions…"
      empty-text="No open positions"
    >
      <template #toolbar-left>
        <h2 class="jd-card-title">Open Positions</h2>
      </template>
      <template #cell:pnl="{ value }">
        <span :class="value >= 0 ? 'price-up' : 'price-down'">{{ value >= 0 ? '+' : '' }}{{ value }}%</span>
      </template>
      <template #row-actions>
        <Button label="Close" icon="pi pi-times" class="jd-btn jd-btn-ghost jd-btn-sm"></Button>
      </template>
    </DataTable>

    <!-- Trade History -->
    <DataTable
      :columns="tradeColumns"
      :data="tradeHistory"
      :row-key="(row) => `${row.timestamp}-${row.symbol}`"
      :searchable="['symbol']"
      search-placeholder="Search trades…"
      empty-text="No trades executed yet"
    >
      <template #toolbar-left>
        <h2 class="jd-card-title">Trade History</h2>
      </template>
      <template #cell:side="{ value }">
        <span class="jd-badge" :class="value === 'Buy' ? 'green' : 'red'">{{ value }}</span>
      </template>
    </DataTable>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import DataTable from '@/components/common/DataTable.vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import InputNumber from 'primevue/inputnumber'
import Dropdown from 'primevue/dropdown'
import Calendar from 'primevue/calendar'
import Tag from 'primevue/tag'

const simulationMode = ref('historical')
const startDate = ref(null)
const endDate = ref(null)

const modes = ref([
  { label: 'Historical Data', value: 'historical' },
  { label: 'Live Paper Trading', value: 'live' },
  { label: 'Custom Dataset', value: 'custom' }
])

const orderTypes = ref([
  { label: 'Limit', value: 'limit' },
  { label: 'Market', value: 'market' }
])

const orderSides = ref([
  { label: 'Buy', value: 'buy' },
  { label: 'Sell', value: 'sell' }
])

const openPositions = ref([])
const tradeHistory = ref([])

const positionColumns = [
  { key: 'symbol', header: 'Symbol', sortable: true },
  { key: 'amount', header: 'Amount', sortable: true, align: 'right' },
  { key: 'entryPrice', header: 'Entry Price', sortable: true, align: 'right' },
  { key: 'currentPrice', header: 'Current Price', sortable: true, align: 'right' },
  { key: 'pnl', header: 'P/L', sortable: true, align: 'right' },
]

const tradeColumns = [
  { key: 'symbol', header: 'Symbol', sortable: true, filterable: true, filterLabel: 'Symbol' },
  { key: 'side', header: 'Side', sortable: true, align: 'center', filterable: true, filterLabel: 'Side' },
  { key: 'price', header: 'Price', sortable: true, align: 'right' },
  { key: 'amount', header: 'Amount', sortable: true, align: 'right' },
  { key: 'total', header: 'Total', sortable: true, align: 'right' },
  { key: 'timestamp', header: 'Time', sortable: true },
]
</script>

<style scoped>
.w-full {
  width: 100%;
}

.price-up {
  color: var(--jd-green);
}

.price-down {
  color: var(--jd-red);
}

:deep(.p-datatable) {
  background-color: transparent;
  color: var(--jd-text);
}

:deep(.p-datatable .p-datatable-thead > tr > th) {
  background-color: transparent;
  color: var(--jd-text);
  border-color: var(--jd-border);
  font-weight: 600;
}

:deep(.p-datatable .p-datatable-tbody > tr) {
  border-color: var(--jd-border);
}

:deep(.p-datatable .p-datatable-tbody > tr > td) {
  border-color: var(--jd-border);
  color: var(--jd-text);
}

:deep(.p-inputtext.jd-input),
:deep(.p-inputnumber.jd-input) {
  background-color: var(--jd-card);
  border: 1px solid var(--jd-border);
  color: var(--jd-text);
  padding: 8px 12px;
  border-radius: 6px;
}

:deep(.p-inputtext.jd-input:focus),
:deep(.p-inputnumber.jd-input:focus) {
  border-color: var(--jd-blue);
  outline: none;
}

:deep(.p-dropdown.jd-select) {
  background-color: var(--jd-card);
  border: 1px solid var(--jd-border);
  color: var(--jd-text);
  border-radius: 6px;
}

:deep(.p-dropdown.jd-select:focus) {
  border-color: var(--jd-blue);
  outline: none;
}

:deep(.p-dropdown.jd-select .p-dropdown-trigger) {
  color: var(--jd-text-muted);
}

:deep(.p-calendar.jd-input) {
  background-color: var(--jd-card);
  border: 1px solid var(--jd-border);
  color: var(--jd-text);
  border-radius: 6px;
}

:deep(.p-calendar.jd-input:focus) {
  border-color: var(--jd-blue);
  outline: none;
}

:deep(.p-button.jd-btn) {
  background-color: var(--jd-blue);
  border-color: var(--jd-blue);
  color: white;
  padding: 8px 16px;
  border-radius: 6px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s;
}

:deep(.p-button.jd-btn:hover) {
  opacity: 0.8;
}

:deep(.p-button.jd-btn-success) {
  background-color: var(--jd-green);
  border-color: var(--jd-green);
}

:deep(.p-button.jd-btn-danger) {
  background-color: var(--jd-red);
  border-color: var(--jd-red);
}

:deep(.p-button.jd-btn-ghost) {
  background-color: transparent;
  border: 1px solid var(--jd-border);
  color: var(--jd-text);
}

:deep(.p-button.jd-btn-ghost:hover) {
  background-color: var(--jd-card);
}

:deep(.p-button.jd-btn-lg) {
  padding: 12px 24px;
  font-size: 16px;
}

:deep(.p-button.jd-btn-sm) {
  padding: 4px 8px;
  font-size: 12px;
}
</style>
