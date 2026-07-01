<template>
  <div class="jd-page">
    <!-- Analysis Controls -->
    <div class="jd-card">
      <div class="jd-card-body">
        <div class="grid grid-cols-1 md:grid-cols-4 gap-4">
          <div>
            <label style="display: block; color: var(--jd-text-muted); font-size: 0.875rem; margin-bottom: 8px">Select Symbol</label>
            <InputText placeholder="Symbol..." class="w-full" />
          </div>
          <div>
            <label style="display: block; color: var(--jd-text-muted); font-size: 0.875rem; margin-bottom: 8px">Timeframe</label>
            <Dropdown
              :options="timeframes"
              optionLabel="label"
              optionValue="value"
              placeholder="Select timeframe"
              class="w-full"
            />
          </div>
          <div>
            <label style="display: block; color: var(--jd-text-muted); font-size: 0.875rem; margin-bottom: 8px">Model</label>
            <Dropdown
              :options="models"
              optionLabel="label"
              optionValue="value"
              placeholder="Select model"
              class="w-full"
            />
          </div>
          <div class="flex items-end">
            <Button label="Run Analysis" icon="pi pi-play" class="w-full"></Button>
          </div>
        </div>
      </div>
    </div>

    <!-- Analysis Results Grid -->
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
      <!-- Sentiment Analysis -->
      <div class="jd-card">
        <div class="jd-card-header">
          <h3 class="jd-card-title">Sentiment Analysis</h3>
        </div>
        <div class="jd-card-body">
          <div style="display: flex; flex-direction: column; gap: 16px">
            <div style="text-align: center">
              <p style="font-size: 3rem; font-weight: bold; color: var(--jd-blue)">--</p>
              <p style="color: var(--jd-text-muted); font-size: 0.875rem; margin-top: 8px">Sentiment Score</p>
            </div>
            <div style="background: rgba(0, 0, 0, 0.2); padding: 12px; border-radius: 4px; text-align: center">
              <p style="color: var(--jd-text-muted); font-size: 0.875rem">No analysis available</p>
            </div>
          </div>
        </div>
      </div>

      <!-- Price Prediction -->
      <div class="jd-card">
        <div class="jd-card-header">
          <h3 class="jd-card-title">Price Prediction</h3>
        </div>
        <div class="jd-card-body">
          <div style="display: flex; flex-direction: column; gap: 16px">
            <div>
              <p style="color: var(--jd-text-muted); font-size: 0.875rem; margin-bottom: 4px">7-Day Forecast</p>
              <p style="font-size: 1.5rem; font-weight: bold; color: var(--jd-text)">--</p>
            </div>
            <div>
              <p style="color: var(--jd-text-muted); font-size: 0.875rem; margin-bottom: 4px">Confidence</p>
              <ProgressBar :value="0" class="mb-2"></ProgressBar>
            </div>
          </div>
        </div>
      </div>

      <!-- Model Performance -->
      <div class="jd-card">
        <div class="jd-card-header">
          <h3 class="jd-card-title">Model Performance</h3>
        </div>
        <div class="jd-card-body">
          <div style="display: flex; flex-direction: column; gap: 12px">
            <div>
              <p style="color: var(--jd-text-muted); font-size: 0.875rem">Accuracy</p>
              <p style="font-size: 1.25rem; font-weight: bold; color: var(--jd-text)">-- %</p>
            </div>
            <div>
              <p style="color: var(--jd-text-muted); font-size: 0.875rem">Win Rate</p>
              <p style="font-size: 1.25rem; font-weight: bold; color: var(--jd-text)">-- %</p>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Detailed Report -->
    <div class="jd-card">
      <div class="jd-card-header">
        <h2 class="jd-card-title">Analysis Report</h2>
      </div>
      <div class="jd-card-body">
        <div style="text-align: center; padding: 48px 16px">
          <p style="color: var(--jd-text-muted)">Run an analysis to view detailed results and insights</p>
        </div>
      </div>
    </div>

    <!-- Key Metrics -->
    <div class="jd-card">
      <div class="jd-card-header">
        <h2 class="jd-card-title">Key Metrics</h2>
      </div>
      <div class="jd-card-body">
        <DataTable
          :columns="metricColumns"
          :data="metrics"
          :row-key="(r) => r.metric"
          :pagination="false"
          empty-text="No metrics available"
        >
          <template #cell:status="{ value }">
            <Tag
              :value="value"
              :severity="value === 'Bullish' ? 'success' : value === 'Bearish' ? 'danger' : 'info'"
            ></Tag>
          </template>
        </DataTable>
        <div style="margin-top: 24px; text-align: center">
          <p style="color: var(--jd-text-muted)">No metrics available</p>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import DataTable from '@/components/common/DataTable.vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Dropdown from 'primevue/dropdown'
import Tag from 'primevue/tag'
import ProgressBar from 'primevue/progressbar'

const timeframes = ref([
  { label: '1H', value: '1h' },
  { label: '4H', value: '4h' },
  { label: '1D', value: '1d' },
  { label: '1W', value: '1w' }
])

const models = ref([
  { label: 'LSTM', value: 'lstm' },
  { label: 'GRU', value: 'gru' },
  { label: 'Transformer', value: 'transformer' }
])

const metricColumns = [
  { key: 'metric', header: 'Metric' },
  { key: 'value', header: 'Value' },
  { key: 'status', header: 'Status' },
]

const metrics = ref([])
</script>

<style scoped>
:deep(.p-inputtext) {
  background-color: rgba(75, 85, 99, 0.5);
  border-color: var(--jd-border);
  color: var(--jd-text);
}

:deep(.p-dropdown) {
  background-color: rgba(75, 85, 99, 0.5);
  border-color: var(--jd-border);
  color: var(--jd-text);
}

:deep(.p-dropdown .p-dropdown-trigger) {
  color: var(--jd-text-muted);
}

:deep(.p-button) {
  background-color: var(--jd-blue);
  border-color: var(--jd-blue);
}

:deep(.p-button:hover) {
  background-color: var(--jd-blue);
  opacity: 0.85;
}
</style>
