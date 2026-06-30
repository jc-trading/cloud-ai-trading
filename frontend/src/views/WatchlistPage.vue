<template>
  <div class="jd-page">
    <!-- Header Stats -->
    <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
      <div class="jd-stat-card">
        <div class="jd-stat-icon" style="background: rgba(59, 130, 246, 0.1);">
          <i class="pi pi-list" style="color: var(--jd-blue);"></i>
        </div>
        <div class="jd-stat-label">Total Watched</div>
        <div class="jd-stat-value">{{ watchlistItems.length }}</div>
      </div>
      <div class="jd-stat-card green">
        <div class="jd-stat-icon" style="background: rgba(16, 185, 129, 0.1);">
          <i class="pi pi-arrow-up" style="color: var(--jd-green);"></i>
        </div>
        <div class="jd-stat-label">Gainers</div>
        <div class="jd-stat-value" style="color: var(--jd-green);">{{ gainersCount }}</div>
      </div>
      <div class="jd-stat-card red">
        <div class="jd-stat-icon" style="background: rgba(239, 68, 68, 0.1);">
          <i class="pi pi-arrow-down" style="color: var(--jd-red);"></i>
        </div>
        <div class="jd-stat-label">Losers</div>
        <div class="jd-stat-value" style="color: var(--jd-red);">{{ losersCount }}</div>
      </div>
    </div>

    <!-- Add Symbol Card -->
    <div class="jd-card">
      <div class="jd-card-header">
        <h3 class="jd-card-title">Add to Watchlist</h3>
      </div>
      <div class="jd-card-body">
        <div class="flex gap-3">
          <input
            v-model="newSymbol"
            type="text"
            placeholder="Enter symbol (e.g., BTCUSDT, AAPL)..."
            class="jd-input flex-1"
            @keyup.enter="addSymbol"
          />
          <select
            v-model="newMarketType"
            class="jd-input"
          >
            <option value="crypto">Crypto</option>
            <option value="stock">Stock</option>
          </select>
          <button
            @click="addSymbol"
            :disabled="!newSymbol || addingSymbol"
            class="jd-btn jd-btn-primary"
          >
            <i class="pi pi-plus"></i>
            {{ addingSymbol ? 'Adding...' : 'Add' }}
          </button>
        </div>
      </div>
    </div>

    <!-- Watchlist Table Card -->
    <div class="jd-card">
      <div class="jd-card-body p-0">
        <div class="overflow-x-auto">
          <table class="jd-table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Type</th>
                <th style="text-align: right;">Last Price</th>
                <th style="text-align: right;">24h Change</th>
                <th style="text-align: right;">Action</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in watchlistItems" :key="item.id">
                <td>
                  <div class="flex items-center gap-3">
                    <div class="w-8 h-8 rounded-full bg-gradient-to-br from-blue-500 to-blue-600 flex items-center justify-center text-white text-xs font-bold">
                      {{ item.symbol.charAt(0) }}
                    </div>
                    <span class="font-medium text-white">{{ item.symbol }}</span>
                  </div>
                </td>
                <td>
                  <span :class="['jd-badge', item.market_type === 'crypto' ? 'blue' : 'green']">
                    {{ item.market_type === 'crypto' ? 'Crypto' : 'Stock' }}
                  </span>
                </td>
                <td style="text-align: right; font-weight: 600; color: var(--jd-text);">
                  {{ item.last_price ? `$${parseFloat(item.last_price).toFixed(2)}` : 'N/A' }}
                </td>
                <td style="text-align: right;">
                  <span :class="['font-medium', parseFloat(item.change_24h) >= 0 ? 'price-up' : 'price-down']">
                    <i :class="['pi', parseFloat(item.change_24h) >= 0 ? 'pi-arrow-up' : 'pi-arrow-down', 'mr-1']"></i>
                    {{ item.change_24h ? parseFloat(item.change_24h).toFixed(2) : '0' }}%
                  </span>
                </td>
                <td style="text-align: right;">
                  <button
                    @click="removeSymbol(item.id)"
                    :disabled="removingId === item.id"
                    class="jd-btn jd-btn-danger jd-btn-sm"
                  >
                    <i class="pi pi-trash"></i>
                    {{ removingId === item.id ? 'Removing...' : 'Remove' }}
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- Empty State -->
        <div v-if="watchlistItems.length === 0" class="jd-empty">
          <i class="pi pi-inbox"></i>
          <p>No symbols in your watchlist yet</p>
          <p>Add your first symbol above to get started</p>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { message } from 'ant-design-vue'
import axios from 'axios'

const watchlistItems = ref([])
const newSymbol = ref('')
const newMarketType = ref('crypto')
const addingSymbol = ref(false)
const removingId = ref(null)
const loadingItems = ref(true)

const gainersCount = computed(() => {
  return watchlistItems.value.filter(item => parseFloat(item.change_24h) > 0).length
})

const losersCount = computed(() => {
  return watchlistItems.value.filter(item => parseFloat(item.change_24h) < 0).length
})

const loadWatchlist = async () => {
  try {
    loadingItems.value = true
    const response = await axios.get('/api/v1/system/watchlist/prices')
    watchlistItems.value = response.data || []
  } catch (error) {
    console.error('Failed to load watchlist:', error)
    message.error('Failed to load watchlist')
  } finally {
    loadingItems.value = false
  }
}

const addSymbol = async () => {
  if (!newSymbol.value.trim()) {
    message.warning('Please enter a symbol')
    return
  }

  addingSymbol.value = true
  try {
    await axios.post('/api/v1/system/watchlist/items', {
      symbol: newSymbol.value.toUpperCase(),
      market_type: newMarketType.value
    })
    message.success(`${newSymbol.value} added to watchlist`)
    newSymbol.value = ''
    await loadWatchlist()
  } catch (error) {
    console.error('Failed to add symbol:', error)
    message.error('Failed to add symbol to watchlist')
  } finally {
    addingSymbol.value = false
  }
}

const removeSymbol = async (itemId) => {
  removingId.value = itemId
  try {
    await axios.delete(`/api/v1/system/watchlist/items/${itemId}`)
    message.success('Symbol removed from watchlist')
    await loadWatchlist()
  } catch (error) {
    console.error('Failed to remove symbol:', error)
    message.error('Failed to remove symbol')
  } finally {
    removingId.value = null
  }
}

onMounted(() => {
  loadWatchlist()
})
</script>

<style scoped>
/* ── Design System Variables ──────────────────────────────── */
:root {
  --jd-text: #f3f4f6;
  --jd-text-muted: #9ca3af;
  --jd-border: #1f2937;
  --jd-blue: #3b82f6;
  --jd-green: #10b981;
  --jd-red: #ef4444;
  --jd-yellow: #f59e0b;
}

/* ── Page Layout ───────────────────────────────────────────── */
.jd-page {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

/* ── Stat Cards ────────────────────────────────────────────── */
.jd-stat-card {
  background-color: rgba(31, 41, 55, 0.6);
  border: 1px solid var(--jd-border);
  border-radius: 0.5rem;
  padding: 1.5rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.jd-stat-icon {
  width: 2.5rem;
  height: 2.5rem;
  border-radius: 0.5rem;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1.25rem;
}
.jd-stat-label {
  font-size: 0.875rem;
  color: var(--jd-text-muted);
  font-weight: 500;
}
.jd-stat-value {
  font-size: 1.875rem;
  font-weight: bold;
  color: var(--jd-text);
}
.jd-stat-sub {
  font-size: 0.75rem;
  color: var(--jd-text-muted);
}

/* ── Cards ─────────────────────────────────────────────────── */
.jd-card {
  background-color: rgba(31, 41, 55, 0.6);
  border: 1px solid var(--jd-border);
  border-radius: 0.5rem;
  overflow: hidden;
}
.jd-card-header {
  padding: 1.25rem;
  border-bottom: 1px solid var(--jd-border);
  background-color: rgba(17, 24, 39, 0.5);
}
.jd-card-title {
  font-size: 1.125rem;
  font-weight: bold;
  color: var(--jd-text);
}
.jd-card-body {
  padding: 1.5rem;
}
.jd-card-body.p-0 {
  padding: 0;
}

/* ── Form Inputs ───────────────────────────────────────────── */
.jd-input {
  flex: 1;
  padding: 0.5rem 1rem;
  background-color: rgba(55, 65, 81, 0.6);
  border: 1px solid var(--jd-border);
  border-radius: 0.375rem;
  color: var(--jd-text);
  font-size: 0.875rem;
  transition: all 0.15s ease;
}
.jd-input:focus {
  outline: none;
  border-color: var(--jd-blue);
  box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.25);
}
.jd-input::placeholder {
  color: var(--jd-text-muted);
}
.jd-label {
  display: block;
  font-size: 0.875rem;
  font-weight: 500;
  color: var(--jd-text);
  margin-bottom: 0.5rem;
}

/* ── Table ─────────────────────────────────────────────────── */
.jd-table {
  width: 100%;
  border-collapse: collapse;
  color: var(--jd-text);
}
.jd-table thead tr {
  background-color: rgba(17, 24, 39, 0.8);
  border-bottom: 1px solid var(--jd-border);
}
.jd-table th {
  padding: 1rem 1.5rem;
  text-align: left;
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--jd-text-muted);
}
.jd-table tbody tr {
  border-bottom: 1px solid var(--jd-border);
  transition: background-color 0.15s ease;
}
.jd-table tbody tr:hover {
  background-color: rgba(55, 65, 81, 0.3);
}
.jd-table td {
  padding: 1rem 1.5rem;
  color: var(--jd-text);
}

/* ── Badges ────────────────────────────────────────────────── */
.jd-badge {
  display: inline-block;
  font-size: 0.75rem;
  padding: 0.375rem 0.75rem;
  border-radius: 9999px;
  font-weight: 500;
}
.jd-badge.green {
  background-color: rgba(16, 185, 129, 0.2);
  color: #10b981;
}
.jd-badge.red {
  background-color: rgba(239, 68, 68, 0.2);
  color: #ef4444;
}
.jd-badge.blue {
  background-color: rgba(59, 130, 246, 0.2);
  color: #60a5fa;
}
.jd-badge.yellow {
  background-color: rgba(245, 158, 11, 0.2);
  color: #fbbf24;
}
.jd-badge.gray {
  background-color: rgba(107, 114, 128, 0.2);
  color: #d1d5db;
}

/* ── Buttons ───────────────────────────────────────────────── */
.jd-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  padding: 0.5rem 1rem;
  border-radius: 0.375rem;
  font-weight: 500;
  border: none;
  cursor: pointer;
  transition: all 0.15s ease;
  font-size: 0.875rem;
}
.jd-btn-primary {
  background-color: var(--jd-blue);
  color: white;
}
.jd-btn-primary:hover:not(:disabled) {
  background-color: #2563eb;
}
.jd-btn-danger {
  background-color: rgba(239, 68, 68, 0.2);
  color: var(--jd-red);
  border: 1px solid var(--jd-red);
}
.jd-btn-danger:hover:not(:disabled) {
  background-color: rgba(239, 68, 68, 0.3);
}
.jd-btn-ghost {
  background-color: transparent;
  color: var(--jd-text);
  border: 1px solid var(--jd-border);
}
.jd-btn-ghost:hover:not(:disabled) {
  background-color: rgba(75, 85, 99, 0.4);
}
.jd-btn-sm {
  padding: 0.375rem 0.75rem;
  font-size: 0.75rem;
}
.jd-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* ── Empty State ───────────────────────────────────────────── */
.jd-empty {
  text-align: center;
  padding: 3rem 1.5rem;
}
.jd-empty i {
  font-size: 3rem;
  color: rgba(107, 114, 128, 0.5);
  display: block;
  margin-bottom: 1rem;
}
.jd-empty p {
  margin: 0.5rem 0;
  color: var(--jd-text-muted);
}
.jd-empty p:first-of-type {
  font-size: 1.125rem;
  font-weight: 500;
  color: var(--jd-text);
}

/* ── Price Changes ─────────────────────────────────────────── */
.price-up {
  color: var(--jd-green);
}
.price-down {
  color: var(--jd-red);
}
</style>
