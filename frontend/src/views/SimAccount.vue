<template>
  <div>
    <div class="jd-page-head">
      <div>
        <h1 class="jd-h1">Sim Accounts</h1>
        <p class="jd-sub">You vs the system — same $2,000 start, same cost model, simulation only</p>
      </div>
      <button type="button" class="jd-btn jd-btn-ghost jd-btn-sm" :disabled="loading" @click="load">
        <i class="pi pi-refresh"></i> Refresh
      </button>
    </div>

    <div v-if="error" class="jd-decision nogo" style="padding:24px 22px" role="alert">
      <div class="jd-take" style="margin-bottom:6px"><b>Could not load the accounts.</b></div>
      <button type="button" class="jd-btn jd-btn-ghost jd-btn-sm" @click="load">Try again</button>
    </div>

    <div v-else class="acct-grid">
      <!-- One panel per account -->
      <section v-for="acct in panels" :key="acct.key" class="jd-decision" :class="acct.key === 'system' ? 'watch' : 'go'" style="cursor:default">
        <div class="d-head">
          <span class="d-sym">{{ acct.title }}</span>
          <span v-if="acct.data?.is_system" class="jd-badge purple">system</span>
          <span class="jd-verdict" :class="retClass(acct)">{{ retLabel(acct) }}</span>
        </div>

        <div class="d-body" v-if="acct.data">
          <div class="d-right" style="flex:1">
            <div class="jd-metrics">
              <div class="jd-metric"><div class="k">equity</div><div class="v">${{ equityOf(acct.data).toFixed(2) }}</div></div>
              <div class="jd-metric"><div class="k">cash</div><div class="v">${{ acct.data.cash.toFixed(2) }}</div></div>
              <div class="jd-metric"><div class="k">open lots</div><div class="v">{{ acct.data.positions.length }}</div></div>
              <div class="jd-metric"><div class="k">start</div><div class="v">${{ acct.data.starting_capital.toFixed(0) }}</div></div>
            </div>
            <!-- equity curve sparkline -->
            <div class="jd-wave" style="margin-top:10px" v-if="acct.data.equity_curve.length > 1">
              <svg viewBox="0 0 300 38" preserveAspectRatio="none">
                <polyline :points="curvePoints(acct.data.equity_curve)" fill="none"
                          stroke="var(--jd-cyan)" stroke-width="1.5"
                          style="filter: drop-shadow(0 0 4px var(--jd-cyan))" />
              </svg>
            </div>
            <p v-else class="d-nodata" style="margin-top:10px">Equity curve appears after the first daily snapshot.</p>

            <!-- positions -->
            <div class="jd-table-wrap" style="margin-top:12px" v-if="acct.data.positions.length">
              <table class="jd-table">
                <thead><tr><th>Symbol</th><th>Shares</th><th>Avg cost</th><th>Stop</th><th>Since</th><th v-if="acct.key==='mine'"></th></tr></thead>
                <tbody>
                  <tr v-for="p in acct.data.positions" :key="p.symbol">
                    <td><b>{{ p.symbol }}</b></td>
                    <td>{{ p.shares.toFixed(4) }}</td>
                    <td>{{ p.avg_cost.toFixed(2) }}</td>
                    <td>{{ p.stop.toFixed(2) }}</td>
                    <td style="font-size:12px;color:var(--jd-dim)">{{ p.entry_date }}</td>
                    <td v-if="acct.key==='mine'">
                      <button type="button" class="jd-btn jd-btn-ghost jd-btn-sm" :disabled="trading" @click="sell(p)">Sell</button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
        <div v-else class="d-body"><p class="d-nodata">Not available yet.</p></div>

        <!-- manual trade form (practice account only) -->
        <div class="d-foot" v-if="acct.key === 'mine'">
          <form class="trade-form" @submit.prevent="buy">
            <input v-model="form.symbol" class="jd-input" placeholder="Symbol" maxlength="10" required style="width:110px;text-transform:uppercase" />
            <input v-model.number="form.qty" class="jd-input" type="number" step="any" min="0.0001" placeholder="Qty" required style="width:90px" />
            <input v-model.number="form.stop" class="jd-input" type="number" step="any" min="0.01" placeholder="Stop (required)" required style="width:130px" />
            <button type="submit" class="jd-btn jd-btn-sm" :disabled="trading">Buy @ market</button>
            <span class="jd-src">fills at live quote ± costs · stop mandatory</span>
          </form>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useToast } from '@/composables/useToast'
import { getSimAccount, placeSimTrade } from '../api/sim'

const toast = useToast()
const mine = ref(null)
const system = ref(null)
const loading = ref(false)
const trading = ref(false)
const error = ref(false)
const form = ref({ symbol: '', qty: null, stop: null })

const panels = computed(() => [
  { key: 'mine', title: 'My practice account', data: mine.value },
  { key: 'system', title: '对照 · system account', data: system.value },
])

const equityOf = a => {
  const curve = a.equity_curve
  const marked = a.positions.reduce((s, p) => s + p.shares * p.avg_cost, 0)
  return curve.length ? curve[curve.length - 1].equity : a.cash + marked
}
const retLabel = acct => {
  if (!acct.data) return '—'
  const r = equityOf(acct.data) / acct.data.starting_capital - 1
  return (r >= 0 ? '+' : '') + (r * 100).toFixed(1) + '%'
}
const retClass = acct => {
  if (!acct.data) return 'watch'
  return equityOf(acct.data) >= acct.data.starting_capital ? 'go' : 'nogo'
}
const curvePoints = curve => {
  const vals = curve.map(c => c.equity)
  const min = Math.min(...vals); const max = Math.max(...vals)
  const span = max - min || 1
  return vals.map((v, i) =>
    `${(i / (vals.length - 1)) * 300},${36 - ((v - min) / span) * 34}`).join(' ')
}

async function load() {
  loading.value = true
  error.value = false
  try {
    const { data } = await getSimAccount()
    mine.value = data.mine
    system.value = data.system
  } catch (e) {
    error.value = true
  } finally {
    loading.value = false
  }
}

async function buy() {
  trading.value = true
  try {
    const { data } = await placeSimTrade({
      symbol: form.value.symbol.toUpperCase(), side: 'buy',
      qty: form.value.qty, stop: form.value.stop,
    })
    toast.success(`Bought ${data.qty} ${data.symbol} @ ~${data.raw_price.toFixed(2)}`)
    form.value = { symbol: '', qty: null, stop: null }
    await load()
  } catch (e) {
    toast.error(e?.response?.data?.detail || 'Trade failed')
  } finally {
    trading.value = false
  }
}

async function sell(p) {
  trading.value = true
  try {
    await placeSimTrade({ symbol: p.symbol, side: 'sell', qty: p.shares })
    toast.success(`Closed ${p.symbol}`)
    await load()
  } catch (e) {
    toast.error(e?.response?.data?.detail || 'Close failed')
  } finally {
    trading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
/* Shared page chrome (.jd-page-head, .d-head, …) lives in assets/main.css. */
.acct-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 18px; }
/* Padding lives here (not on the global .d-foot) — Recommendations' .d-foot
   gets its padding from the inner .d-reason instead. */
.trade-form { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; padding: 12px 16px; }
</style>
