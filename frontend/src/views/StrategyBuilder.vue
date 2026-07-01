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
      <TabView class="strategy-tabs">
        <TabPanel header="Create New Strategy">
          <!-- Strategy Creation -->
          <div class="jd-card-body" style="display: flex; flex-direction: column; gap: 24px;">
            <!-- Strategy Name -->
            <div class="jd-form-group">
              <label class="jd-label">Strategy Name</label>
              <InputText v-model="strategyName" placeholder="e.g., MA Crossover Strategy" class="w-full jd-input" />
            </div>

            <!-- Entry Conditions -->
            <div>
              <h3 style="font-size: 18px; font-weight: bold; color: var(--jd-text); margin-bottom: 16px;">Entry Conditions</h3>
              <div style="display: flex; flex-direction: column; gap: 12px; background: var(--jd-card); border: 1px solid var(--jd-border); border-radius: 8px; padding: 16px;">
                <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px;">
                  <div class="jd-form-group">
                    <label class="jd-label">Indicator 1</label>
                    <Dropdown
                      :options="indicators"
                      optionLabel="label"
                      optionValue="value"
                      placeholder="Select indicator"
                      class="w-full jd-select"
                    />
                  </div>
                  <div class="jd-form-group">
                    <label class="jd-label">Condition</label>
                    <Dropdown
                      :options="conditions"
                      optionLabel="label"
                      optionValue="value"
                      placeholder="Select condition"
                      class="w-full jd-select"
                    />
                  </div>
                  <div class="jd-form-group">
                    <label class="jd-label">Value</label>
                    <InputText placeholder="Enter value" class="w-full jd-input" />
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
                    <InputNumber placeholder="2.0" class="w-full jd-input" />
                  </div>
                  <div class="jd-form-group">
                    <label class="jd-label">Stop Loss (%)</label>
                    <InputNumber placeholder="1.0" class="w-full jd-input" />
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
                    <InputNumber placeholder="5" class="w-full jd-input" />
                  </div>
                  <div class="jd-form-group">
                    <label class="jd-label">Max Drawdown (%)</label>
                    <InputNumber placeholder="10" class="w-full jd-input" />
                  </div>
                </div>
              </div>
            </div>

            <!-- Action Buttons -->
            <div style="display: flex; gap: 16px; padding-top: 16px;">
              <Button label="Save Strategy" icon="pi pi-save" class="jd-btn jd-btn-primary"></Button>
              <Button label="Backtest" icon="pi pi-play" class="jd-btn jd-btn-ghost"></Button>
            </div>
          </div>
        </TabPanel>

        <TabPanel header="My Strategies">
          <!-- Strategies List -->
          <div class="jd-card-body">
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
                  <Button label="Edit" icon="pi pi-pencil" class="jd-btn jd-btn-ghost jd-btn-sm"></Button>
                  <Button label="Backtest" icon="pi pi-chart-bar" class="jd-btn jd-btn-ghost jd-btn-sm"></Button>
                  <Button label="Delete" icon="pi pi-trash" class="jd-btn jd-btn-danger jd-btn-sm"></Button>
                </div>
              </template>
            </DataTable>
          </div>
        </TabPanel>

        <TabPanel header="Backtest Results">
          <!-- Backtest Results -->
          <div class="jd-card-body">
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
        </TabPanel>
      </TabView>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import DataTable from '@/components/common/DataTable.vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import InputNumber from 'primevue/inputnumber'
import Dropdown from 'primevue/dropdown'
import Tag from 'primevue/tag'
import TabView from 'primevue/tabview'
import TabPanel from 'primevue/tabpanel'

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

.strategy-tabs {
  background-color: transparent;
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

:deep(.p-button.jd-btn-ghost) {
  background-color: transparent;
  border: 1px solid var(--jd-border);
  color: var(--jd-text);
}

:deep(.p-button.jd-btn-ghost:hover) {
  background-color: var(--jd-card);
}

:deep(.p-button.jd-btn-danger) {
  background-color: var(--jd-red);
  border-color: var(--jd-red);
}

:deep(.p-button.jd-btn-sm) {
  padding: 4px 8px;
  font-size: 12px;
}

:deep(.p-tabview) {
  background-color: transparent;
}

:deep(.p-tabview .p-tabview-nav) {
  background-color: transparent;
  border-color: var(--jd-border);
}

:deep(.p-tabview .p-tabview-nav button) {
  color: var(--jd-text-muted);
  border-radius: 4px;
  padding: 8px 16px;
}

:deep(.p-tabview .p-tabview-nav button.p-highlight) {
  color: var(--jd-text);
  border-color: var(--jd-blue);
  background-color: transparent;
}

:deep(.p-tabview .p-tabview-panels) {
  padding: 0;
}
</style>
