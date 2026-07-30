<template>
  <div class="jd-page">
    <!-- Header -->
    <div>
      <router-link to="/settings" style="color: var(--jd-blue); text-decoration: none; font-size: 13px; display: inline-block; margin-bottom: 12px;">
        ← Back to Settings
      </router-link>
      <h1 class="jd-section-title" style="margin-bottom: 8px;">Exchange API Settings</h1>
      <p style="color: var(--jd-text-muted); font-size: 14px;">Connect your Alpaca account and manage API keys</p>
    </div>

    <!-- Alpaca Card -->
    <div class="jd-card">
      <div class="jd-card-header exchange-header">
        <div style="display: flex; align-items: center; gap: 12px;">
          <div class="exchange-icon alpaca-icon">AP</div>
          <div>
            <h2 class="jd-card-title">Alpaca</h2>
            <p style="color: var(--jd-text-muted); font-size: 12px; margin-top: 2px;">US Stocks — Commission-Free Paper & Live Trading</p>
          </div>
        </div>
        <span class="jd-badge" :class="alpaca.connected ? 'green' : 'red'">
          {{ alpaca.connected ? 'Connected' : 'Disconnected' }}
        </span>
      </div>
      <div class="jd-card-body">
        <!-- Info Alert -->
        <div class="jd-alert info" style="margin-bottom: 16px;">
          <i class="pi pi-info-circle" style="margin-right: 8px;"></i>
          Sign up free at <a href="https://alpaca.markets" target="_blank" style="color: var(--jd-blue); text-decoration: underline;">alpaca.markets</a>.
          Use <strong>Paper Trading</strong> keys for simulation, <strong>Live Trading</strong> keys for real money.
        </div>

        <div class="form-grid">
          <div class="jd-form-group">
            <label class="jd-label">API Key ID</label>
            <input
              class="jd-input"
              v-model="alpaca.apiKey"
              placeholder="PKXXXXXXXXXXXXXXXX"
              :disabled="alpaca.connected"
            />
          </div>

          <div class="jd-form-group">
            <label class="jd-label">API Secret Key</label>
            <input
              type="password"
              class="jd-input"
              v-model="alpaca.apiSecret"
              placeholder="Paste your Alpaca secret key"
              :disabled="alpaca.connected"
            />
          </div>
        </div>

        <!-- Trading Mode -->
        <div style="margin-top: 16px;">
          <label style="display: block; font-size: 13px; font-weight: 500; color: var(--jd-text-muted); margin-bottom: 10px;">Trading Mode</label>
          <div style="display: flex; gap: 20px;">
            <div style="display: flex; align-items: center; gap: 8px;">
              <input type="radio" value="simulate" v-model="alpaca.tradingMode" name="alpaca-mode" id="a-sim" />
              <label for="a-sim" style="cursor: pointer; color: var(--jd-text); font-size: 13px;">Paper Trading (Simulate)</label>
            </div>
            <div style="display: flex; align-items: center; gap: 8px;">
              <input type="radio" value="live" v-model="alpaca.tradingMode" name="alpaca-mode" id="a-live" />
              <label for="a-live" style="cursor: pointer; color: var(--jd-text); font-size: 13px;">Live Trading (Real Money)</label>
            </div>
          </div>
        </div>

        <!-- Action Buttons -->
        <div style="display: flex; gap: 8px; margin-top: 20px; padding-top: 16px; border-top: 1px solid var(--jd-border); flex-wrap: wrap;">
          <button
            class="jd-btn jd-btn-ghost jd-btn-sm"
            :disabled="!alpaca.apiKey || !alpaca.apiSecret || alpacaSaving || alpacaTesting"
            @click="testAlpacaConnection"
          >
            <i v-if="alpacaTesting" class="pi pi-spin pi-spinner"></i>
            Test Connection
          </button>
          <button
            v-if="!alpaca.connected"
            class="jd-btn jd-btn-primary jd-btn-sm"
            :disabled="!alpaca.apiKey || !alpaca.apiSecret || alpacaTesting || alpacaSaving"
            @click="connectAlpaca"
          >
            <i v-if="alpacaSaving" class="pi pi-spin pi-spinner"></i>
            Connect
          </button>
          <button
            v-if="alpaca.connected"
            class="jd-btn jd-btn-primary jd-btn-sm"
            :disabled="alpacaSaving"
            @click="updateAlpaca"
          >
            <i v-if="alpacaSaving" class="pi pi-spin pi-spinner"></i>
            Update Mode
          </button>
          <button
            v-if="alpaca.connected"
            class="jd-btn jd-btn-danger jd-btn-sm"
            @click="disconnectAlpaca"
          >
            Disconnect
          </button>
        </div>

        <div v-if="alpaca.connected" class="jd-alert success" style="margin-top: 12px;">
          <i class="pi pi-check-circle" style="margin-right: 8px;"></i>
          Alpaca connected — US stock trading enabled.
        </div>

        <div v-if="alpacaTestResult" :class="['jd-alert', alpacaTestResult.ok ? 'success' : 'error']" style="margin-top: 12px;">
          <i :class="alpacaTestResult.ok ? 'pi pi-check-circle' : 'pi pi-times-circle'" style="margin-right: 8px;"></i>
          {{ alpacaTestResult.message }}
        </div>
      </div>
    </div>

    <!-- Connected Exchanges Table -->
    <div class="jd-card" v-if="connectedList.length > 0">
      <div class="jd-card-header">
        <h2 class="jd-card-title">Connected Exchanges</h2>
      </div>
      <div class="jd-card-body">
        <DataTable :columns="connectedColumns" :data="connectedList" :pagination="false" empty-text="No connected exchanges">
          <template #cell:exchange_type="{ value }">
            <span style="text-transform: capitalize; font-weight: 500; color: var(--jd-text);">{{ value }}</span>
          </template>
          <template #cell:trading_mode="{ value }">
            <span class="jd-badge" :class="value === 'live' ? 'red' : 'blue'">{{ value }}</span>
          </template>
          <template #cell:last_synced_at="{ value }">
            {{ value ? new Date(value).toLocaleString() : 'Never' }}
          </template>
          <template #row-actions="{ row }">
            <button class="jd-btn jd-btn-ghost jd-btn-sm" @click="fetchBalance(row.id)">Get Balance</button>
          </template>
        </DataTable>
      </div>
    </div>

    <!-- Security Note Alert -->
    <div class="jd-alert info" style="display: flex; gap: 12px; align-items: flex-start;">
      <i class="pi pi-shield" style="margin-top: 2px; flex-shrink: 0;"></i>
      <div>
        <h3 style="font-weight: 600; color: var(--jd-text); margin-bottom: 4px;">Security Note</h3>
        <p style="font-size: 12px; color: var(--jd-text-muted); line-height: 1.5;">
          Your API keys are encrypted with AES-256 (Fernet) before being stored in the database.
          Use keys with <strong>trading only</strong> permissions — no withdrawal rights.
          Never share your API secret with anyone.
        </p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useToast } from '@/composables/useToast'
import DataTable from '@/components/common/DataTable.vue'
import { exchangeApi } from '@/api/exchange'

const toast = useToast()

// ── Connected Exchanges table columns ────────────────────────────
const connectedColumns = [
  { key: 'exchange_type', header: 'Exchange' },
  { key: 'trading_mode', header: 'Mode' },
  { key: 'last_synced_at', header: 'Last Sync' },
]

// ── State ────────────────────────────────────────────────────────
const connectedList = ref([])

// ── Alpaca State ─────────────────────────────────────────────────
const alpaca = ref({
  id: null,
  apiKey: '',
  apiSecret: '',
  tradingMode: 'simulate',
  connected: false,
})
const alpacaTesting = ref(false)
const alpacaSaving = ref(false)
const alpacaTestResult = ref(null)

// ── Load on mount ────────────────────────────────────────────────
onMounted(async () => {
  try {
    const res = await exchangeApi.list()
    const all = res.data || []
    connectedList.value = all

    const existingAlpaca = all.find(e => e.exchange_type === 'alpaca')
    if (existingAlpaca) {
      alpaca.value.id          = existingAlpaca.id
      alpaca.value.tradingMode = existingAlpaca.trading_mode
      alpaca.value.connected   = true
    }
  } catch (e) {
    // Not fatal
  }
})

// ── Alpaca functions ─────────────────────────────────────────────
async function testAlpacaConnection() {
  alpacaTestResult.value = null
  alpacaTesting.value = true
  try {
    let id = alpaca.value.id
    if (!id) {
      const created = await exchangeApi.create({
        exchange_type: 'alpaca',
        api_key:       alpaca.value.apiKey,
        api_secret:    alpaca.value.apiSecret,
        trading_mode:  alpaca.value.tradingMode,
      })
      id = created.data.id
      alpaca.value.id = id
    }
    const res = await exchangeApi.test(id)
    const ok  = res.data?.success !== false
    alpacaTestResult.value = {
      ok,
      message: ok ? 'Alpaca connection verified!' : `Failed: ${res.data?.message || 'Unknown error'}`,
    }
    toast.add({ severity: ok ? 'success' : 'error', summary: ok ? 'Connected' : 'Failed', detail: alpacaTestResult.value.message, life: 4000 })
  } catch (err) {
    const msg = err.response?.data?.detail || err.message || 'Request failed'
    alpacaTestResult.value = { ok: false, message: msg }
    toast.add({ severity: 'error', summary: 'Test Failed', detail: msg, life: 5000 })
  } finally {
    alpacaTesting.value = false
  }
}

async function connectAlpaca() {
  alpacaSaving.value = true
  try {
    const res = await exchangeApi.create({
      exchange_type: 'alpaca',
      api_key:       alpaca.value.apiKey,
      api_secret:    alpaca.value.apiSecret,
      trading_mode:  alpaca.value.tradingMode,
    })
    alpaca.value.id        = res.data.id
    alpaca.value.connected = true
    connectedList.value = [...connectedList.value, res.data]
    toast.add({ severity: 'success', summary: 'Alpaca Connected', detail: 'US stock trading enabled.', life: 3000 })
  } catch (err) {
    const msg = err.response?.data?.detail || err.message || 'Failed to save'
    toast.add({ severity: 'error', summary: 'Connect Failed', detail: msg, life: 5000 })
  } finally {
    alpacaSaving.value = false
  }
}

async function updateAlpaca() {
  if (!alpaca.value.id) return
  alpacaSaving.value = true
  try {
    await exchangeApi.update(alpaca.value.id, { trading_mode: alpaca.value.tradingMode })
    toast.add({ severity: 'success', summary: 'Updated', detail: 'Trading mode updated.', life: 3000 })
  } catch (err) {
    toast.add({ severity: 'error', summary: 'Update Failed', detail: err.response?.data?.detail || 'Error', life: 5000 })
  } finally {
    alpacaSaving.value = false
  }
}

async function disconnectAlpaca() {
  if (!alpaca.value.id) return
  if (!window.confirm('Disconnect Alpaca? Your stored API keys will be deleted.')) return
  try {
    await exchangeApi.remove(alpaca.value.id)
    alpaca.value = { id: null, apiKey: '', apiSecret: '', tradingMode: 'simulate', connected: false }
    connectedList.value = connectedList.value.filter(e => e.exchange_type !== 'alpaca')
    toast.add({ severity: 'info', summary: 'Disconnected', detail: 'Alpaca connection removed.', life: 3000 })
  } catch (err) {
    toast.add({ severity: 'error', summary: 'Error', detail: err.response?.data?.detail || 'Error', life: 5000 })
  }
}

// ── Get Balance ──────────────────────────────────────────────────
async function fetchBalance(id) {
  try {
    const res = await exchangeApi.getBalance(id)
    const total = res.data?.total_usdt ? `Total: ${res.data.total_usdt} USDT` : 'Balance fetched'
    toast.add({ severity: 'success', summary: 'Balance', detail: total, life: 4000 })
  } catch (err) {
    const msg = err.response?.data?.detail || 'Could not fetch balance'
    toast.add({ severity: 'error', summary: 'Balance Error', detail: msg, life: 4000 })
  }
}
</script>

<style scoped>
.form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 16px;
}

.exchange-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.exchange-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 700;
  flex-shrink: 0;
}

.alpaca-icon {
  background-color: #eab308;
  color: #000;
}

.jd-section-title {
  font-size: 28px;
  font-weight: 700;
  color: var(--jd-text);
}

.jd-label {
  display: block;
  font-size: 13px;
  font-weight: 500;
  color: var(--jd-text-muted);
  margin-bottom: 6px;
}

.jd-form-group {
  display: flex;
  flex-direction: column;
}

.jd-badge {
  display: inline-block;
  padding: 4px 10px;
  border-radius: 12px;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  white-space: nowrap;
}

.jd-badge.green {
  background-color: #10b981;
  color: white;
}

.jd-badge.red {
  background-color: #ef4444;
  color: white;
}

.jd-badge.blue {
  background-color: var(--jd-blue);
  color: white;
}

.jd-alert {
  padding: 12px;
  border-radius: 4px;
  font-size: 12px;
  display: flex;
  gap: 8px;
}

.jd-alert.success {
  background-color: rgba(16, 185, 129, 0.1);
  color: #10b981;
  border: 1px solid rgba(16, 185, 129, 0.3);
}

.jd-alert.error {
  background-color: rgba(239, 68, 68, 0.1);
  color: #ef4444;
  border: 1px solid rgba(239, 68, 68, 0.3);
}

.jd-alert.info {
  background-color: rgba(59, 130, 246, 0.1);
  color: var(--jd-text);
  border: 1px solid rgba(59, 130, 246, 0.3);
}

.jd-btn-primary {
  background-color: var(--jd-blue);
  color: white;
  border: 1px solid var(--jd-blue);
}

.jd-btn-primary:hover:not(:disabled) {
  background-color: #2563eb;
  border-color: #2563eb;
}

.jd-btn-ghost {
  background-color: transparent;
  color: var(--jd-text);
  border: 1px solid var(--jd-border);
}

.jd-btn-ghost:hover:not(:disabled) {
  background-color: var(--jd-card);
  border-color: var(--jd-text-muted);
}

.jd-btn-danger {
  background-color: #ef4444;
  color: white;
  border: 1px solid #ef4444;
}

.jd-btn-danger:hover:not(:disabled) {
  background-color: #dc2626;
  border-color: #dc2626;
}

.jd-btn-sm {
  padding: 6px 12px;
  font-size: 12px;
}

:deep(.p-datatable-thead > tr > th) {
  background-color: var(--jd-card);
  color: var(--jd-text);
  border-color: var(--jd-border);
  font-weight: 600;
  font-size: 12px;
}

:deep(.p-datatable-tbody > tr > td) {
  border-color: var(--jd-border);
  color: var(--jd-text);
  font-size: 13px;
}

:deep(.p-datatable-tbody > tr:hover) {
  background-color: rgba(59, 130, 246, 0.05);
}
</style>
