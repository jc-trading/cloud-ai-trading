<template>
  <div>
    <div class="jd-page-head">
      <div>
        <h1 class="jd-h1">{{ mode === 'clone' ? `Clone ${source?.name || 'group'}` : 'New strategy group' }}</h1>
      </div>
      <div class="head-actions">
        <router-link :to="mode === 'clone' ? `/groups/${sourceId}` : '/groups'" class="jd-btn jd-btn-ghost jd-btn-sm">
          <i class="pi pi-arrow-left"></i> Back
        </router-link>
      </div>
    </div>

    <div v-if="loadError" class="jd-decision nogo" style="padding:24px 22px" role="alert">
      <div class="jd-take" style="margin-bottom:6px"><b>{{ loadError }}</b></div>
      <button type="button" class="jd-btn jd-btn-ghost jd-btn-sm" @click="load">Try again</button>
    </div>

    <div v-else-if="loading || !registry" class="jd-empty"><p>Loading…</p></div>

    <form v-else class="group-form" novalidate @submit.prevent="submit">
      <div v-if="!auth.isAdmin" class="jd-alert warning">
        <i class="pi pi-lock"></i> Only admins can create or clone groups.
      </div>
      <div v-if="saveError" class="jd-alert error" role="alert">
        <i class="pi pi-exclamation-triangle"></i> {{ saveError }}
      </div>

      <section class="jd-card">
        <div class="jd-card-header"><h2 class="jd-card-title">Group</h2></div>
        <div class="jd-card-body">
          <div class="row-2">
            <div class="jd-form-group">
              <label class="jd-label" for="group-name">Name</label>
              <input id="group-name" v-model="name" class="jd-input" maxlength="100" required />
              <p v-if="fieldErrors.name" class="field-error">{{ fieldErrors.name }}</p>
            </div>
            <div class="jd-form-group">
              <label class="jd-label" for="group-strategy">Strategy</label>
              <select v-if="mode === 'create'" id="group-strategy" v-model="strategyKey" class="jd-input jd-select"
                      @change="resetParams">
                <option v-for="s in registry.strategies" :key="s.key" :value="s.key">{{ s.key }}</option>
              </select>
              <input v-else id="group-strategy" class="jd-input jd-mono" :value="source.strategy_key" readonly />
              <div v-if="strategy" class="jd-src" style="margin-top:3px">{{ strategy.description }}</div>
            </div>
          </div>
          <div class="row-2">
            <div class="jd-form-group">
              <label class="jd-label" for="group-capital">Starting capital</label>
              <input id="group-capital" v-model.number="startingCapital" class="jd-input jd-mono" type="number" step="any"
                     :placeholder="mode === 'clone' ? String(source.account.starting_capital) : ''" />
              <p v-if="fieldErrors.starting_capital" class="field-error">{{ fieldErrors.starting_capital }}</p>
            </div>
            <div class="jd-form-group">
              <div class="jd-label">Status</div>
              <div class="radios">
                <label><input v-model="status" type="radio" value="paused" /> paused</label>
                <label><input v-model="status" type="radio" value="active" /> active</label>
              </div>
              <p v-if="fieldErrors.status" class="field-error">{{ fieldErrors.status }}</p>
            </div>
          </div>
          <div class="jd-form-group">
            <label class="jd-label" for="group-notes">Notes</label>
            <textarea id="group-notes" v-model="notes" class="jd-input" rows="2" maxlength="2000"></textarea>
            <p v-if="fieldErrors.notes" class="field-error">{{ fieldErrors.notes }}</p>
          </div>
          <div v-if="mode === 'clone'" class="jd-form-group">
            <label class="archive-source">
              <input v-model="archiveSource" type="checkbox" :disabled="source.status !== 'paused'" />
              <span class="jd-label" style="margin:0">Archive source after cloning</span>
            </label>
            <div v-if="source.status !== 'paused'" class="jd-src" style="margin-top:3px">
              source must be paused (and flat, outside RTH)
            </div>
          </div>
        </div>
      </section>

      <section class="jd-card">
        <div class="jd-card-header"><h2 class="jd-card-title">Params</h2></div>
        <div class="jd-card-body">
          <ParamsForm
            v-if="strategy"
            v-model="params"
            :schema="strategy.params_schema"
            :cross-rules="strategy.cross_rules"
            :base="paramsBase"
            :errors="fieldErrors"
          />
        </div>
      </section>

      <section class="jd-card">
        <div class="jd-card-header"><h2 class="jd-card-title">Screener</h2></div>
        <div class="jd-card-body">
          <ScreenerBuilder
            v-model="screener"
            :vocab="registry.screener"
            :errors="fieldErrors"
            @touched="screenerTouched = true"
          />
        </div>
      </section>

      <section class="jd-card">
        <div class="jd-card-header"><h2 class="jd-card-title">Trade settings</h2></div>
        <div class="jd-card-body trade-body">
          <div v-if="trade.calibration_run_id !== ''" class="jd-alert warning">
            <i class="pi pi-exclamation-triangle"></i>
            Changing params, screener or other trade settings makes this calibration run invalid (409).
          </div>
          <TradeSettingsForm
            v-model="trade"
            :registry="registry.trade_settings"
            :calibration-runs="calibrationRuns"
            :picker-available="mode === 'clone'"
            :errors="fieldErrors"
            @touched="tradeTouched = true"
          />
        </div>
      </section>

      <div class="form-foot">
        <button type="submit" class="jd-btn jd-btn-primary" :disabled="saving || !auth.isAdmin">
          <i v-if="saving" class="pi pi-spin pi-spinner"></i>
          {{ mode === 'clone' ? 'Clone group' : 'Create group' }}
        </button>
      </div>
    </form>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import ParamsForm from '@/components/groups/ParamsForm.vue'
import ScreenerBuilder from '@/components/groups/ScreenerBuilder.vue'
import TradeSettingsForm, { toTradeSettingsJson } from '@/components/groups/TradeSettingsForm.vue'
import { getRegistry, getStrategyGroup, createGroup, cloneGroup } from '@/api/strategyGroups'
import { listBacktests, getBacktest } from '@/api/backtests'
import { useAuthStore } from '@/stores/auth'
import { useStrategyGroupsStore } from '@/stores/strategyGroups'
import { useToast } from '@/composables/useToast'
import { detailOf, fieldErrorsOf } from '@/composables/useApiError'

const props = defineProps({
  mode: { type: String, default: 'create' },
  sourceId: { type: String, default: null },
})

const router = useRouter()
const auth = useAuthStore()
const toast = useToast()

const registry = ref(null)
const source = ref(null)
const loading = ref(false)
const loadError = ref(null)
const saving = ref(false)
const saveError = ref(null)
const fieldErrors = ref({})

const name = ref('')
const strategyKey = ref('')
const notes = ref('')
const startingCapital = ref('')
const status = ref('paused')
const archiveSource = ref(false)
const params = ref({})
const screener = ref({})
const screenerTouched = ref(false)
const TRADE_KEYS = ['entry_limit_offset_pct', 'take_profit_pct', 'max_hold_days', 'holding_style', 'calibration_run_id', 'entry_prob_min']
const trade = ref({})
const tradeTouched = ref(false)
const calibrationRuns = ref([])

const strategy = computed(() =>
  registry.value?.strategies.find((s) => s.key === strategyKey.value) || null)

const schemaDefaults = (schema) => Object.fromEntries(Object.entries(schema).map(
  ([section, fields]) => [section, Object.fromEntries(
    Object.entries(fields).map(([field, spec]) => [field, spec.default]))]))

const paramsBase = computed(() => {
  if (props.mode === 'clone') return source.value?.params_json || {}
  return strategy.value ? schemaDefaults(strategy.value.params_schema) : {}
})

function resetParams() {
  const schema = strategy.value?.params_schema || {}
  params.value = Object.fromEntries(Object.entries(schema).map(
    ([section, fields]) => [section, Object.fromEntries(Object.keys(fields).map((f) => [f, '']))]))
}

async function load() {
  loading.value = true
  loadError.value = null
  saveError.value = null
  fieldErrors.value = {}
  try {
    const [reg, src] = await Promise.all([
      getRegistry(),
      props.mode === 'clone' ? getStrategyGroup(props.sourceId) : null,
    ])
    registry.value = reg.data
    source.value = src?.data ?? null
    name.value = ''
    notes.value = ''
    status.value = 'paused'
    archiveSource.value = false
    screenerTouched.value = false
    tradeTouched.value = false
    calibrationRuns.value = []
    if (props.mode === 'clone') {
      strategyKey.value = source.value.strategy_key
      startingCapital.value = ''
      params.value = JSON.parse(JSON.stringify(source.value.params_json))
      screener.value = JSON.parse(JSON.stringify(source.value.screener_json))
      const ts = source.value.trade_settings_json || {}
      trade.value = Object.fromEntries(TRADE_KEYS.map((k) => [k, ts[k] ?? (k === 'holding_style' ? 'swing' : '')]))
      loadCalibrationRuns(props.sourceId, ts.calibration_run_id || '')
    } else {
      strategyKey.value = registry.value.strategies[0]?.key || ''
      startingCapital.value = 2000
      resetParams()
      screener.value = JSON.parse(JSON.stringify(registry.value.screener.default))
      trade.value = Object.fromEntries(TRADE_KEYS.map((k) => [k, k === 'holding_style' ? 'swing' : '']))
    }
  } catch (e) {
    loadError.value = detailOf(e, props.mode === 'clone' ? 'Could not load the source group.' : 'Could not load the registry.')
  } finally {
    loading.value = false
  }
}

async function loadCalibrationRuns(sourceId, currentId) {
  try {
    const { data } = await listBacktests({ groupId: sourceId, status: 'succeeded', limit: 50 })
    const ids = data.filter((r) => r.kind === 'walk_forward').map((r) => r.id)
    if (currentId && !ids.includes(currentId)) ids.push(currentId)
    const runs = await Promise.all(ids.map((id) => getBacktest(id).then((r) => r.data).catch(() => null)))
    if (sourceId !== props.sourceId) return
    calibrationRuns.value = runs.filter(Boolean).map((r) => {
      const blocks = r.calibration_json?.blocks || []
      return {
        id: r.id,
        label: `${r.data_start}..${r.data_end} · finished ${(r.finished_at || '').slice(0, 10)}`,
        maxP: blocks.length ? Math.max(...blocks.map((b) => b.p)) : null,
        usable: r.status === 'succeeded' && r.kind === 'walk_forward'
          && r.calibration_json?.status === 'ok' && !r.config_snapshot?.calibration_ref,
      }
    }).filter((r) => r.usable || r.id === currentId)
  } catch {
    if (sourceId === props.sourceId) calibrationRuns.value = []
  }
}

function resolveGroupField(piece) {
  let m = piece.match(/\b(strategy|funnel|exits)\.([a-z_]+)/)
  if (m) return `params.${m[1]}.${m[2]}`
  m = piece.match(/^screener_json\.rules\[(\d+)\]/)
  if (m) return `screener.rules.${m[1]}`
  m = piece.match(/^screener_json\.(include|exclude|asset_types)/)
  if (m) return `screener.${m[1]}`
  if (piece.startsWith('screener_json')) return 'screener'
  m = piece.match(/^trade_settings_json\.([a-z_]+)/)
  if (m) return `trade.${m[1]}`
  if (piece.startsWith('trade_settings_json') || piece.startsWith('calibration run')) return 'trade.calibration_run_id'
  for (const key of ['name', 'notes', 'starting_capital', 'status']) {
    if (piece.startsWith(key)) return key
  }
  if (piece.startsWith('params_json') || piece.startsWith('unknown strategy_key')) return 'params'
  return null
}

function paramsOverride(onlyChanged) {
  const out = {}
  for (const [section, fields] of Object.entries(params.value)) {
    const kept = {}
    for (const [field, value] of Object.entries(fields)) {
      if (value === '') continue
      if (onlyChanged && value === source.value.params_json?.[section]?.[field]) continue
      kept[field] = value
    }
    if (Object.keys(kept).length) out[section] = kept
  }
  return out
}

function buildBody() {
  if (props.mode === 'clone') {
    const override = paramsOverride(true)
    return {
      name: name.value,
      params_json: Object.keys(override).length ? override : null,
      screener_json: screenerTouched.value ? screener.value : null,
      trade_settings_json: tradeTouched.value ? toTradeSettingsJson(trade.value) : null,
      notes: notes.value || null,
      starting_capital: startingCapital.value === '' ? null : startingCapital.value,
      status: status.value,
      archive_source: archiveSource.value,
    }
  }
  return {
    name: name.value,
    strategy_key: strategyKey.value,
    params_json: paramsOverride(false),
    screener_json: screener.value,
    trade_settings_json: toTradeSettingsJson(trade.value),
    notes: notes.value || null,
    starting_capital: Number(startingCapital.value),
    status: status.value,
  }
}

async function submit() {
  if (saving.value) return
  saving.value = true
  saveError.value = null
  fieldErrors.value = {}
  try {
    const body = buildBody()
    const { data } = props.mode === 'clone'
      ? await cloneGroup(props.sourceId, body)
      : await createGroup(body)
    toast.success(`Group ${data.name} created`)
    await useStrategyGroupsStore().ensureLoaded(true)
    router.push('/groups/' + data.id)
  } catch (e) {
    saveError.value = detailOf(e, 'Save failed')
    fieldErrors.value = fieldErrorsOf(e, resolveGroupField)
  } finally {
    saving.value = false
  }
}

onMounted(load)
watch(() => [props.mode, props.sourceId], load)
</script>

<style scoped>
.head-actions { display: flex; align-items: flex-end; gap: 10px; flex-wrap: wrap; }
.group-form { display: flex; flex-direction: column; gap: 18px; }
.row-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.radios { display: flex; gap: 18px; padding-top: 8px; font-size: 13px; }
.radios label, .archive-source { display: flex; align-items: center; gap: 8px; cursor: pointer; }
.trade-body { display: flex; flex-direction: column; gap: 14px; }
.form-foot { display: flex; justify-content: flex-end; gap: 10px; }
.field-error { color: var(--jd-red); font-size: 12px; margin: 4px 0 0; }
@media (max-width: 720px) {
  .row-2 { grid-template-columns: 1fr; }
}
</style>
