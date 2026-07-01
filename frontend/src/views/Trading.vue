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
        <button class="jd-btn jd-btn-primary"><i class="pi pi-link"></i> Connect Exchange</button>
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
                <input v-model="orderSymbol" placeholder="BTCUSDT" class="w-full jd-input" />
              </div>

              <!-- Order Type and Side -->
              <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
                <div class="jd-form-group">
                  <label class="jd-label">Order Type</label>
                  <select v-model="orderType" class="w-full jd-input jd-select">
                    <option v-for="o in orderTypes" :key="o.value" :value="o.value">{{ o.label }}</option>
                  </select>
                </div>
                <div class="jd-form-group">
                  <label class="jd-label">Side</label>
                  <select v-model="orderSide" class="w-full jd-input jd-select">
                    <option v-for="o in orderSides" :key="o.value" :value="o.value">{{ o.label }}</option>
                  </select>
                </div>
              </div>

              <!-- Price and Amount -->
              <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
                <div class="jd-form-group">
                  <label class="jd-label">Price (USDT)</label>
                  <input type="number" placeholder="0.00" class="w-full jd-input" />
                </div>
                <div class="jd-form-group">
                  <label class="jd-label">Amount</label>
                  <input type="number" placeholder="0.00" class="w-full jd-input" />
                </div>
              </div>

              <!-- Total -->
              <div style="background: var(--jd-card); border: 1px solid var(--jd-border); border-radius: 8px; padding: 12px; display: flex; justify-content: space-between; align-items: center;">
                <span style="color: var(--jd-text-muted);">Total (USDT)</span>
                <span style="font-weight: bold; color: var(--jd-text);">0.00</span>
              </div>

              <!-- Advanced Options -->
              <div style="border-top: 1px solid var(--jd-border); padding-top: 16px; display: flex; align-items: center; gap: 8px;">
                <input type="checkbox" v-model="advancedOptions" id="advanced" />
                <label for="advanced" class="jd-label">Show Advanced Options</label>
              </div>

              <!-- Advanced Options Content -->
              <div v-if="advancedOptions" style="display: flex; flex-direction: column; gap: 16px; background: var(--jd-card); border: 1px solid var(--jd-border); border-radius: 8px; padding: 16px;">
                <div class="jd-form-group">
                  <label class="jd-label">Stop Loss</label>
                  <input type="number" placeholder="0.00" class="w-full jd-input" />
                </div>
                <div class="jd-form-group">
                  <label class="jd-label">Take Profit</label>
                  <input type="number" placeholder="0.00" class="w-full jd-input" />
                </div>
              </div>

              <!-- Action Buttons -->
              <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px; padding-top: 16px;">
                <button class="jd-btn jd-btn-success jd-btn-lg"><i class="pi pi-arrow-up"></i> Buy</button>
                <button class="jd-btn jd-btn-danger jd-btn-lg"><i class="pi pi-arrow-down"></i> Sell</button>
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
          <DataTable
            :columns="openOrderColumns"
            :data="openOrders"
            row-key="symbol"
            :searchable="['symbol']"
            search-placeholder="Search open orders…"
            :page-size="10"
            empty-text="No open orders"
          >
            <template #cell:side="{ value }">
              <span class="jd-badge" :class="value === 'Buy' ? 'green' : 'red'">{{ value }}</span>
            </template>
            <template #row-actions>
              <button class="jd-btn jd-btn-danger jd-btn-sm"><i class="pi pi-times"></i> Cancel</button>
            </template>
          </DataTable>
        </div>
      </div>

      <!-- Order History -->
      <div class="jd-card">
        <div class="jd-card-header">
          <h2 class="jd-card-title">Order History</h2>
        </div>
        <div class="jd-card-body">
          <DataTable
            :columns="orderHistoryColumns"
            :data="orderHistory"
            row-key="symbol"
            :searchable="['symbol']"
            search-placeholder="Search order history…"
            :page-size="10"
            empty-text="No order history"
          >
            <template #cell:side="{ value }">
              <span class="jd-badge" :class="value === 'Buy' ? 'green' : 'red'">{{ value }}</span>
            </template>
          </DataTable>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import DataTable from '@/components/common/DataTable.vue'

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

const openOrderColumns = [
  { key: 'symbol', header: 'Symbol', sortable: true, filterable: true, filterLabel: 'Symbol' },
  { key: 'side', header: 'Side', sortable: true, align: 'center', filterable: true, filterLabel: 'Side' },
  { key: 'price', header: 'Price', sortable: true, align: 'right' },
  { key: 'amount', header: 'Amount', sortable: true, align: 'right' },
  { key: 'status', header: 'Status', sortable: true, align: 'right', filterable: true, filterLabel: 'Status' },
]

const orderHistoryColumns = [
  { key: 'symbol', header: 'Symbol', sortable: true, filterable: true, filterLabel: 'Symbol' },
  { key: 'side', header: 'Side', sortable: true, align: 'center', filterable: true, filterLabel: 'Side' },
  { key: 'price', header: 'Price', sortable: true, align: 'right' },
  { key: 'filledAmount', header: 'Filled', sortable: true, align: 'right' },
  { key: 'status', header: 'Status', sortable: true, align: 'right', filterable: true, filterLabel: 'Status' },
  { key: 'timestamp', header: 'Time', sortable: true, align: 'right' },
]
</script>

<style scoped>
.w-full {
  width: 100%;
}
</style>
