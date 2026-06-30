<template>
  <div class="jd-page">
    <!-- Header -->
    <div class="jd-section-header">
      <div>
        <h1 class="jd-section-title">Live Trading</h1>
        <p class="jd-section-description">Execute live trades on connected exchanges</p>
      </div>
    </div>

    <!-- Connection Status -->
    <div class="jd-card">
      <div class="jd-card-body" style="display: flex; align-items: center; justify-content: space-between;">
        <div style="display: flex; align-items: center; gap: 12px;">
          <div class="jd-live-dot"></div>
          <span>Exchange Status: <span style="color: var(--jd-red);">Disconnected</span></span>
        </div>
        <Button label="Connect Exchange" icon="pi pi-link" class="jd-btn jd-btn-primary"></Button>
      </div>
    </div>

    <!-- Trading Panel -->
    <div style="display: grid; grid-template-columns: 1fr; gap: 20px; max-width: 100%;">
      <div style="display: grid; grid-template-columns: 2fr 1fr; gap: 20px;">
        <!-- Order Entry -->
        <div class="jd-card">
          <div class="jd-card-header">
            <h2 class="jd-card-title">Order Entry</h2>
          </div>
          <div class="jd-card-body">
            <div style="display: flex; flex-direction: column; gap: 16px;">
              <!-- Symbol Selection -->
              <div class="jd-form-group">
                <label class="jd-label">Symbol</label>
                <InputText v-model="orderSymbol" placeholder="BTCUSDT" class="w-full jd-input" />
              </div>

              <!-- Order Type and Side -->
              <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
                <div class="jd-form-group">
                  <label class="jd-label">Order Type</label>
                  <Dropdown
                    v-model="orderType"
                    :options="orderTypes"
                    optionLabel="label"
                    optionValue="value"
                    class="w-full jd-select"
                  />
                </div>
                <div class="jd-form-group">
                  <label class="jd-label">Side</label>
                  <Dropdown
                    v-model="orderSide"
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

              <!-- Advanced Options -->
              <div style="border-top: 1px solid var(--jd-border); padding-top: 16px; display: flex; align-items: center; gap: 8px;">
                <Checkbox v-model="advancedOptions" binary inputId="advanced"></Checkbox>
                <label for="advanced" class="jd-label">Show Advanced Options</label>
              </div>

              <!-- Advanced Options Content -->
              <div v-if="advancedOptions" style="display: flex; flex-direction: column; gap: 16px; background: var(--jd-card); border: 1px solid var(--jd-border); border-radius: 8px; padding: 16px;">
                <div class="jd-form-group">
                  <label class="jd-label">Stop Loss</label>
                  <InputNumber placeholder="0.00" class="w-full jd-input" />
                </div>
                <div class="jd-form-group">
                  <label class="jd-label">Take Profit</label>
                  <InputNumber placeholder="0.00" class="w-full jd-input" />
                </div>
              </div>

              <!-- Action Buttons -->
              <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px; padding-top: 16px;">
                <Button label="Buy" icon="pi pi-arrow-up" class="jd-btn jd-btn-success jd-btn-lg"></Button>
                <Button label="Sell" icon="pi pi-arrow-down" class="jd-btn jd-btn-danger jd-btn-lg"></Button>
              </div>
            </div>
          </div>
        </div>

        <!-- Account Info -->
        <div class="jd-card">
          <div class="jd-card-header">
            <h3 class="jd-card-title">Account Info</h3>
          </div>
          <div class="jd-card-body">
            <div style="display: flex; flex-direction: column; gap: 16px;">
              <div>
                <p style="color: var(--jd-text-muted); font-size: 12px; margin-bottom: 4px;">Balance (USDT)</p>
                <p style="font-size: 24px; font-weight: bold; color: var(--jd-text);">--</p>
              </div>
              <div style="border-top: 1px solid var(--jd-border); padding-top: 16px;">
                <p style="color: var(--jd-text-muted); font-size: 12px; margin-bottom: 4px;">Available</p>
                <p style="font-size: 20px; font-weight: bold; color: var(--jd-text);">--</p>
              </div>
              <div style="border-top: 1px solid var(--jd-border); padding-top: 16px;">
                <p style="color: var(--jd-text-muted); font-size: 12px; margin-bottom: 4px;">In Orders</p>
                <p style="font-size: 20px; font-weight: bold; color: var(--jd-text);">--</p>
              </div>
              <div style="border-top: 1px solid var(--jd-border); padding-top: 16px;">
                <p style="color: var(--jd-text-muted); font-size: 12px; margin-bottom: 4px;">Equity</p>
                <p style="font-size: 20px; font-weight: bold; color: var(--jd-green);">--</p>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Open Orders -->
      <div class="jd-card">
        <div class="jd-card-header">
          <h2 class="jd-card-title">Open Orders</h2>
        </div>
        <div class="jd-card-body">
          <DataTable :value="openOrders" stripedRows responsiveLayout="scroll" class="jd-table p-datatable-sm">
            <Column field="symbol" header="Symbol"></Column>
            <Column field="side" header="Side">
              <template #body="slotProps">
                <span class="jd-badge" :class="slotProps.data.side === 'Buy' ? 'green' : 'red'">
                  {{ slotProps.data.side }}
                </span>
              </template>
            </Column>
            <Column field="price" header="Price"></Column>
            <Column field="amount" header="Amount"></Column>
            <Column field="status" header="Status"></Column>
            <Column header="Actions">
              <template #body="slotProps">
                <Button label="Cancel" icon="pi pi-times" class="jd-btn jd-btn-danger jd-btn-sm"></Button>
              </template>
            </Column>
          </DataTable>
          <div v-if="openOrders.length === 0" class="jd-empty">
            <p>No open orders</p>
          </div>
        </div>
      </div>

      <!-- Order History -->
      <div class="jd-card">
        <div class="jd-card-header">
          <h2 class="jd-card-title">Order History</h2>
        </div>
        <div class="jd-card-body">
          <DataTable :value="orderHistory" stripedRows responsiveLayout="scroll" class="jd-table p-datatable-sm">
            <Column field="symbol" header="Symbol"></Column>
            <Column field="side" header="Side">
              <template #body="slotProps">
                <span class="jd-badge" :class="slotProps.data.side === 'Buy' ? 'green' : 'red'">
                  {{ slotProps.data.side }}
                </span>
              </template>
            </Column>
            <Column field="price" header="Price"></Column>
            <Column field="filledAmount" header="Filled"></Column>
            <Column field="status" header="Status"></Column>
            <Column field="timestamp" header="Time"></Column>
          </DataTable>
          <div v-if="orderHistory.length === 0" class="jd-empty">
            <p>No order history</p>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import InputNumber from 'primevue/inputnumber'
import Dropdown from 'primevue/dropdown'
import Tag from 'primevue/tag'
import Checkbox from 'primevue/checkbox'

const orderSymbol = ref('')
const orderType = ref('limit')
const orderSide = ref('buy')
const advancedOptions = ref(false)

const orderTypes = ref([
  { label: 'Limit', value: 'limit' },
  { label: 'Market', value: 'market' },
  { label: 'Stop Loss', value: 'stop-loss' },
  { label: 'Take Profit', value: 'take-profit' }
])

const orderSides = ref([
  { label: 'Buy', value: 'buy' },
  { label: 'Sell', value: 'sell' }
])

const openOrders = ref([])
const orderHistory = ref([])
</script>

<style scoped>
.w-full {
  width: 100%;
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

:deep(.p-button.jd-btn-success) {
  background-color: var(--jd-green);
  border-color: var(--jd-green);
}

:deep(.p-button.jd-btn-danger) {
  background-color: var(--jd-red);
  border-color: var(--jd-red);
}

:deep(.p-button.jd-btn-lg) {
  padding: 12px 24px;
  font-size: 16px;
}

:deep(.p-button.jd-btn-sm) {
  padding: 4px 8px;
  font-size: 12px;
}

:deep(.p-checkbox) {
  margin-right: 8px;
}
</style>
