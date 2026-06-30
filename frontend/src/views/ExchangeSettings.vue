<template>
  <div class="jd-page">
    <!-- Header -->
    <div>
      <router-link to="/settings" style="color: var(--jd-blue); text-decoration: none; font-size: 13px; display: inline-block; margin-bottom: 12px;">
        ← Back to Settings
      </router-link>
      <h1 class="jd-section-title" style="margin-bottom: 8px;">Exchange API Settings</h1>
      <p style="color: var(--jd-text-muted); font-size: 14px;">Connect your trading exchange accounts and manage API keys</p>
    </div>

    <!-- Binance Card -->
    <div class="jd-card">
      <div class="jd-card-header exchange-header">
        <div style="display: flex; align-items: center; gap: 12px;">
          <div class="exchange-icon binance-icon">B</div>
          <div>
            <h2 class="jd-card-title">Binance</h2>
            <p style="color: var(--jd-text-muted); font-size: 12px; margin-top: 2px;">Spot and Futures Trading</p>
          </div>
        </div>
        <span class="jd-badge" :class="binance.connected ? 'green' : 'red'">
          {{ binance.connected ? 'Connected' : 'Disconnected' }}
        </span>
      </div>
      <div class="jd-card-body">
        <div class="form-grid">
          <!-- API Key -->
          <div class="jd-form-group">
            <label class="jd-label">API Key</label>
            <InputText
              v-model="binance.apiKey"
              placeholder="Paste your Binance API key"
              :disabled="binance.connected"
            />
          </div>

          <!-- API Secret -->
          <div class="jd-form-group">
            <label class="jd-label">API Secret</label>
            <Password
              v-model="binance.apiSecret"
              placeholder="Paste your Binance API secret"
              :feedback="false"
              toggleMask
              inputClass="w-full"
              :disabled="binance.connected"
            />
          </div>
        </div>

        <!-- Trading Mode -->
        <div style="margin-top: 16px;">
          <label style="display: block; font-size: 13px; font-weight: 500; color: var(--jd-text-muted); margin-bottom: 10px;">Trading Mode</label>
          <div style="display: flex; gap: 20px;">
            <div style="display: flex; align-items: center; gap: 8px;">
              <RadioButton v-model="binance.tradingMode" name="binance-mode" value="live" inputId="b-live" />
              <label for="b-live" style="cursor: pointer; color: var(--jd-text); font-size: 13px;">Live Trading</label>
            </div>
            <div style="display: flex; align-items: center; gap: 8px;">
              <RadioButton v-model="binance.tradingMode" name="binance-mode" value="simulate" inputId="b-sim" />
              <label for="b-sim" style="cursor: pointer; color: var(--jd-text); font-size: 13px;">Simulate</label>
            </div>
          </div>
        </div>

        <!-- Action Buttons -->
        <div style="display: flex; gap: 8px; margin-top: 20px; padding-top: 16px; border-top: 1px solid var(--jd-border); flex-wrap: wrap;">
          <Button
            label="Test Connection"
            class="jd-btn jd-btn-ghost jd-btn-sm"
            :loading="testing"
            :disabled="!binance.apiKey || !binance.apiSecret || saving"
            @click="testConnection"
          />
          <Button
            v-if="!binance.connected"
            label="Connect"
            class="jd-btn jd-btn-primary jd-btn-sm"
            :loading="saving"
            :disabled="!binance.apiKey || !binance.apiSecret || testing"
            @click="connectExchange"
          />
          <Button
            v-if="binance.connected"
            label="Update"
            class="jd-btn jd-btn-primary jd-btn-sm"
            :loading="saving"
            :disabled="testing"
            @click="updateExchange"
          />
          <Button
            v-if="binance.connected"
            label="Disconnect"
            class="jd-btn jd-btn-danger jd-btn-sm"
            :loading="disconnecting"
            @click="disconnectExchange"
          />
        </div>

        <!-- Connected info -->
        <div v-if="binance.connected" class="jd-alert success" style="margin-top: 12px;">
          <i class="pi pi-check-circle" style="margin-right: 8px;"></i>
          Connected — keys stored and encrypted. Toggle mode or click Update to change settings.
        </div>

        <!-- Test result -->
        <div v-if="testResult" :class="['jd-alert', testResult.ok ? 'success' : 'error']" style="margin-top: 12px;">
          <i :class="testResult.ok ? 'pi pi-check-circle' : 'pi pi-times-circle'" style="margin-right: 8px;"></i>
          {{ testResult.message }}
        </div>
      </div>
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
            <InputText
              v-model="alpaca.apiKey"
              placeholder="PKXXXXXXXXXXXXXXXX"
              :disabled="alpaca.connected"
            />
          </div>

          <div class="jd-form-group">
            <label class="jd-label">API Secret Key</label>
            <Password
              v-model="alpaca.apiSecret"
              placeholder="Paste your Alpaca secret key"
              :feedback="false"
              toggleMask
              inputClass="w-full"
              :disabled="alpaca.connected"
            />
          </div>
        </div>

        <!-- Trading Mode -->
        <div style="margin-top: 16px;">
          <label style="display: block; font-size: 13px; font-weight: 500; color: var(--jd-text-muted); margin-bottom: 10px;">Trading Mode</label>
          <div style="display: flex; gap: 20px;">
            <div style="display: flex; align-items: center; gap: 8px;">
              <RadioButton v-model="alpaca.tradingMode" name="alpaca-mode" value="simulate" inputId="a-sim" />
              <label for="a-sim" style="cursor: pointer; color: var(--jd-text); font-size: 13px;">Paper Trading (Simulate)</label>
            </div>
            <div style="display: flex; align-items: center; gap: 8px;">
              <RadioButton v-model="alpaca.tradingMode" name="alpaca-mode" value="live" inputId="a-live" />
              <label for="a-live" style="cursor: pointer; color: var(--jd-text); font-size: 13px;">Live Trading (Real Money)</label>
            </div>
          </div>
        </div>

        <!-- Action Buttons -->
        <div style="display: flex; gap: 8px; margin-top: 20px; padding-top: 16px; border-top: 1px solid var(--jd-border); flex-wrap: wrap;">
          <Button
            label="Test Connection"
            class="jd-btn jd-btn-ghost jd-btn-sm"
            :loading="alpacaTesting"
            :disabled="!alpaca.apiKey || !alpaca.apiSecret || alpacaSaving"
            @click="testAlpacaConnection"
          />
          <Button
            v-if="!alpaca.connected"
            label="Connect"
            class="jd-btn jd-btn-primary jd-btn-sm"
            :loading="alpacaSaving"
            :disabled="!alpaca.apiKey || !alpaca.apiSecret || alpacaTesting"
            @click="connectAlpaca"
          />
          <Button
            v-if="alpaca.connected"
            label="Update Mode"
            class="jd-btn jd-btn-primary jd-btn-sm"
            :loading="alpacaSaving"
            @click="updateAlpaca"
          />
          <Button
            v-if="alpaca.connected"
            label="Disconnect"
            class="jd-btn jd-btn-danger jd-btn-sm"
            @click="disconnectAlpaca"
          />
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

    <!-- Bitget Coming Soon -->
    <div class="jd-card" style="opacity: 0.6;">
      <div class="jd-card-header exchange-header">
        <div style="display: flex; align-items: center; gap: 12px;">
          <div class="exchange-icon bitget-icon">BG</div>
          <div>
            <h2 class="jd-card-title">Bitget</h2>
            <p style="color: var(--jd-text-muted); font-size: 12px; margin-top: 2px;">Spot and Futures Trading</p>
          </div>
        </div>
        <span class="jd-badge blue">Coming Soon</span>
      </div>
      <div class="jd-card-body" style="text-align: center; padding: 32px 16px;">
        <p style="color: var(--jd-text-muted); font-size: 13px;">Bitget integration coming soon.</p>
      </div>
    </div>

    <!-- OKX Coming Soon -->
    <div class="jd-card" style="opacity: 0.6;">
      <div class="jd-card-header exchange-header">
        <div style="display: flex; align-items: center; gap: 12px;">
          <div class="exchange-icon okx-icon">OKX</div>
          <div>
            <h2 class="jd-card-title">OKX</h2>
            <p style="color: var(--jd-text-muted); font-size: 12px; margin-top: 2px;">Spot and Futures Trading</p>
          </div>
        </div>
        <span class="jd-badge blue">Coming Soon</span>
      </div>
      <div class="jd-card-body" style="text-align: center; padding: 32px 16px;">
        <p style="color: var(--jd-text-muted); font-size: 13px;">OKX integration coming soon.</p>
      </div>
    </div>

    <!-- Connected Exchanges Table -->
    <div class="jd-card" v-if="connectedList.length > 0">
      <div class="jd-card-header">
        <h2 class="jd-card-title">Connected Exchanges</h2>
      </div>
      <div class="jd-card-body">
        <DataTable :value="connectedList" stripedRows responsiveLayout="scroll" class="p-datatable-sm">
          <Column field="exchange_type" header="Exchange">
            <template #body="{ data }">
              <span style="text-transform: capitalize; font-weight: 500; color: var(--jd-text);">{{ data.exchange_type }}</span>
            </template>
          </Column>
          <Column field="trading_mode" header="Mode">
            <template #body="{ data }">
              <span class="jd-badge" :class="data.trading_mode === 'live' ? 'red' : 'blue'">
                {{ data.trading_mode }}
              </span>
            </template>
          </Column>
          <Column field="last_synced_at" header="Last Sync">
            <template #body="{ data }">
              {{ data.last_synced_at ? new Date(data.last_synced_at).toLocaleString() : 'Never' }}
            </template>
          </Column>
          <Column header="Actions">
            <template #body="{ data }">
              <Button label="Get Balance" class="jd-btn jd-btn-ghost jd-btn-sm" @click="fetchBalance(data.id)" />
            </template>
          </Column>
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

  <Toast />
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useToast } from 'primevue/usetoast'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Password from 'primevue/password'
import RadioButton from 'primevue/radiobutton'
import Tag from 'primevue/tag'
import Toast from 'primevue/toast'
import { exchangeApi } from '@/api/exchange'

const toast = useToast()

// ── State ────────────────────────────────────────────────────────
const binance = ref({
  id: null,
  apiKey: '',
  apiSecret: '',
  tradingMode: 'simulate',
  connected: false,
})

const testing      = ref(false)
const saving       = ref(false)
const disconnecting = ref(false)
const testResult   = ref(null)
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

    const existingBinance = all.find(e => e.exchange_type === 'binance')
    if (existingBinance) {
      binance.value.id          = existingBinance.id
      binance.value.tradingMode = existingBinance.trading_mode
      binance.value.connected   = true
    }

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

// ── Test Connection ──────────────────────────────────────────────
async function testConnection() {
  testResult.value = null
  testing.value = true
  try {
    let id = binance.value.id

    // If not yet saved, create first so we have an ID to test
    if (!id) {
      const created = await exchangeApi.create({
        exchange_type:  'binance',
        api_key:        binance.value.apiKey,
        api_secret:     binance.value.apiSecret,
        trading_mode:   binance.value.tradingMode,
      })
      id = created.data.id
      binance.value.id = id
    }

    const res = await exchangeApi.test(id)
    const ok  = res.data?.success !== false

    testResult.value = {
      ok,
      message: ok
        ? `Connection successful! Account: ${res.data?.account_info?.email || 'Verified'}`
        : `Connection failed: ${res.data?.error || 'Unknown error'}`,
    }
    toast.add({
      severity: ok ? 'success' : 'error',
      summary:  ok ? 'Connection OK' : 'Connection Failed',
      detail:   testResult.value.message,
      life: 4000,
    })
  } catch (err) {
    const msg = err.response?.data?.detail || err.message || 'Request failed'
    testResult.value = { ok: false, message: msg }
    toast.add({ severity: 'error', summary: 'Test Failed', detail: msg, life: 5000 })
  } finally {
    testing.value = false
  }
}

// ── Connect (Save) ───────────────────────────────────────────────
async function connectExchange() {
  saving.value = true
  try {
    const res = await exchangeApi.create({
      exchange_type: 'binance',
      api_key:       binance.value.apiKey,
      api_secret:    binance.value.apiSecret,
      trading_mode:  binance.value.tradingMode,
    })
    binance.value.id        = res.data.id
    binance.value.connected = true
    connectedList.value = [...connectedList.value, res.data]
    toast.add({ severity: 'success', summary: 'Connected', detail: 'Binance API keys saved and encrypted.', life: 3000 })
  } catch (err) {
    const msg = err.response?.data?.detail || err.message || 'Failed to save'
    toast.add({ severity: 'error', summary: 'Connect Failed', detail: msg, life: 5000 })
  } finally {
    saving.value = false
  }
}

// ── Update ───────────────────────────────────────────────────────
async function updateExchange() {
  if (!binance.value.id) return
  saving.value = true
  try {
    // ExchangeUpdate only supports trading_mode, permissions, ip_whitelist, is_active
    await exchangeApi.update(binance.value.id, { trading_mode: binance.value.tradingMode })
    toast.add({ severity: 'success', summary: 'Updated', detail: 'Trading mode updated.', life: 3000 })
  } catch (err) {
    const msg = err.response?.data?.detail || 'Update failed'
    toast.add({ severity: 'error', summary: 'Update Failed', detail: msg, life: 5000 })
  } finally {
    saving.value = false
  }
}

// ── Disconnect ───────────────────────────────────────────────────
async function disconnectExchange() {
  if (!binance.value.id) return
  disconnecting.value = true
  try {
    await exchangeApi.remove(binance.value.id)
    binance.value.id        = null
    binance.value.connected = false
    binance.value.apiKey    = ''
    binance.value.apiSecret = ''
    connectedList.value = connectedList.value.filter(e => e.exchange_type !== 'binance')
    toast.add({ severity: 'info', summary: 'Disconnected', detail: 'Binance connection removed.', life: 3000 })
  } catch (err) {
    const msg = err.response?.data?.detail || 'Disconnect failed'
    toast.add({ severity: 'error', summary: 'Error', detail: msg, life: 5000 })
  } finally {
    disconnecting.value = false
  }
}

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

.binance-icon {
  background-color: #fbbf24;
  color: #000;
}

.alpaca-icon {
  background-color: #eab308;
  color: #000;
}

.bitget-icon {
  background-color: #2563eb;
  color: white;
}

.okx-icon {
  background-color: #000;
  color: #fbbf24;
  border: 1px solid #4b5563;
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

:deep(.p-inputtext),
:deep(.p-password-input) {
  background-color: var(--jd-card);
  border: 1px solid var(--jd-border);
  color: var(--jd-text);
  width: 100%;
  padding: 8px 12px;
  border-radius: 4px;
  font-size: 13px;
}

:deep(.p-inputtext:focus),
:deep(.p-password-input:focus) {
  border-color: var(--jd-blue);
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.1);
}

:deep(.p-inputtext:disabled) {
  background-color: rgba(75, 85, 99, 0.3);
  color: var(--jd-text-muted);
  cursor: not-allowed;
}

:deep(.p-password) {
  width: 100%;
}

:deep(.p-radiobutton .p-radiobutton-box) {
  border: 2px solid var(--jd-border);
  background-color: var(--jd-card);
}

:deep(.p-radiobutton .p-radiobutton-box.p-highlight) {
  background-color: var(--jd-blue);
  border-color: var(--jd-blue);
}

:deep(.p-button) {
  padding: 8px 16px;
  font-size: 13px;
  border-radius: 4px;
  font-weight: 500;
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
