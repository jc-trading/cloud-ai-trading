<template>
  <p v-if="registry?.available !== true" class="d-nodata">Trade settings need the P3c backend.</p>
  <fieldset v-else class="trade" :disabled="disabled" @input="emit('touched')" @change="emit('touched')">
    <template v-if="timeframe !== '1m'">
    <div class="trade-grid">
      <div>
        <label class="jd-label" for="trade-limit">Entry limit offset</label>
        <div class="with-pct">
          <input id="trade-limit" v-model.number="local.entry_limit_offset_pct" class="jd-input jd-mono" type="number" step="any"
                 :min="keys.entry_limit_offset_pct.min" :max="keys.entry_limit_offset_pct.max" />
          <span class="pct">{{ pctOf(local.entry_limit_offset_pct) }}</span>
        </div>
        <div class="jd-src hint">
          [{{ keys.entry_limit_offset_pct.min }}, {{ keys.entry_limit_offset_pct.max }}] · -0.02 = enter only if open ≤ 98% of prior close
        </div>
        <p v-if="errors['trade.entry_limit_offset_pct']" class="field-error">{{ errors['trade.entry_limit_offset_pct'] }}</p>
      </div>
      <div>
        <label class="jd-label" for="trade-tp">Take profit</label>
        <div class="with-pct">
          <input id="trade-tp" v-model.number="local.take_profit_pct" class="jd-input jd-mono" type="number" step="any"
                 :min="keys.take_profit_pct.min" :max="keys.take_profit_pct.max" />
          <span class="pct">{{ pctOf(local.take_profit_pct) }}</span>
        </div>
        <div class="jd-src hint">[{{ keys.take_profit_pct.min }}, {{ keys.take_profit_pct.max }}]</div>
        <p v-if="errors['trade.take_profit_pct']" class="field-error">{{ errors['trade.take_profit_pct'] }}</p>
      </div>
      <div>
        <label class="jd-label" for="trade-hold">Max hold days</label>
        <input id="trade-hold" v-model.number="local.max_hold_days" class="jd-input jd-mono" type="number" step="1"
               :min="keys.max_hold_days.min" :max="keys.max_hold_days.max" />
        <div class="jd-src hint">[{{ keys.max_hold_days.min }}, {{ keys.max_hold_days.max }}]</div>
        <p v-if="errors['trade.max_hold_days']" class="field-error">{{ errors['trade.max_hold_days'] }}</p>
      </div>
      <div>
        <div class="jd-label">Holding style</div>
        <div class="radios">
          <label v-for="s in keys.holding_style.values" :key="s"><input v-model="local.holding_style" type="radio" :value="s" /> {{ s }}</label>
          <label class="off"><input type="radio" disabled /> scalping <span class="jd-src">1m only (P5)</span></label>
        </div>
        <p v-if="errors['trade.holding_style']" class="field-error">{{ errors['trade.holding_style'] }}</p>
      </div>
    </div>
    </template>
    <template v-else>
      <div class="trade-grid">
        <div v-for="[k, spec] in numeric1m" :key="k">
          <label class="jd-label" :for="`trade-${k}`">{{ k }}</label>
          <input :id="`trade-${k}`" v-model.number="local[k]" class="jd-input jd-mono" type="number"
                 :step="spec.type === 'int' ? 1 : 'any'" :min="spec.min ?? spec.gt" :max="spec.max"
                 :placeholder="spec.default == null ? '' : String(spec.default)" />
          <div class="jd-src hint">{{ boundsText(spec) }}<template v-if="spec.excludes"> · not with {{ spec.excludes }}</template></div>
          <p v-if="errors['trade.' + k]" class="field-error">{{ errors['trade.' + k] }}</p>
        </div>
        <div>
          <div class="jd-label">Holding style · session</div>
          <div class="radios">
            <label class="off"><input type="radio" checked disabled /> scalping</label>
            <label class="off"><input type="radio" checked disabled /> rth</label>
          </div>
          <div class="jd-src hint">1m groups: scalping only, regular hours only</div>
        </div>
      </div>
    </template>

    <div class="trade-grid">
      <div>
        <label class="jd-label" for="trade-cal">Calibration run</label>
        <select id="trade-cal" v-model="local.calibration_run_id" class="jd-input jd-select" :disabled="!pickerAvailable">
          <option value="">— none —</option>
          <option v-for="r in calibrationRuns" :key="r.id" :value="r.id">{{ r.label }}{{ r.usable ? '' : ' (unusable)' }}</option>
        </select>
        <div v-if="!pickerAvailable" class="jd-src hint">
          Calibration needs a calibrated walk-forward run of the same config — clone the group that owns it.
        </div>
        <p v-if="errors['trade.calibration_run_id']" class="field-error">{{ errors['trade.calibration_run_id'] }}</p>
      </div>
      <div>
        <label class="jd-label" for="trade-prob">Entry probability min</label>
        <div class="with-pct">
          <input id="trade-prob" v-model.number="local.entry_prob_min" class="jd-input jd-mono" type="number" step="any"
                 :min="keys.entry_prob_min.gt" :max="keys.entry_prob_min.lt" :disabled="local.calibration_run_id === ''" />
          <span class="pct">{{ pctOf(local.entry_prob_min) }}</span>
        </div>
        <div class="jd-src hint">
          ({{ keys.entry_prob_min.gt }}, {{ keys.entry_prob_min.lt }}) open interval
          <template v-if="local.calibration_run_id !== ''"> · max p = {{ selectedMaxP == null ? 'unknown' : selectedMaxP.toFixed(4) }}</template>
        </div>
        <p v-if="errors['trade.entry_prob_min']" class="field-error">{{ errors['trade.entry_prob_min'] }}</p>
      </div>
    </div>
  </fieldset>
</template>

<script>
export function toTradeSettingsJson(m) {
  const out = {}
  for (const k of ['entry_limit_offset_pct', 'take_profit_pct', 'max_hold_days']) {
    if (m[k] !== '') out[k] = m[k]
  }
  if (m.holding_style === 'long') out.holding_style = 'long'
  if (m.calibration_run_id !== '') out.calibration_run_id = m.calibration_run_id
  if (m.entry_prob_min !== '') out.entry_prob_min = m.entry_prob_min
  return out
}
</script>

<script setup>
import { ref, computed, watch } from 'vue'

const props = defineProps({
  registry: { type: Object, default: null },
  modelValue: { type: Object, required: true },
  calibrationRuns: { type: Array, default: () => [] },
  pickerAvailable: { type: Boolean, default: false },
  errors: { type: Object, default: () => ({}) },
  disabled: { type: Boolean, default: false },
  timeframe: { type: String, default: '1d' },
})
const emit = defineEmits(['update:modelValue', 'touched'])

const keys = computed(() => props.registry?.keys || {})
const numeric1m = computed(() => Object.entries(props.registry?.keys_1m || {})
  .filter(([k, s]) => (s.type === 'int' || s.type === 'float') && k !== 'entry_prob_min'))
const boundsText = (s) => (s.gt != null ? `(${s.gt}, ${s.max}]` : `[${s.min}, ${s.max}]`)
const clone = (m) => JSON.parse(JSON.stringify(m))

const local = ref(clone(props.modelValue))
watch(() => props.modelValue, (v) => {
  if (JSON.stringify(v) !== JSON.stringify(local.value)) local.value = clone(v)
}, { deep: true })
watch(local, (v) => emit('update:modelValue', clone(v)), { deep: true })
watch(() => local.value.calibration_run_id, (id) => { if (id === '') local.value.entry_prob_min = '' }, { flush: 'sync' })

const selectedMaxP = computed(() =>
  props.calibrationRuns.find((r) => r.id === local.value.calibration_run_id)?.maxP ?? null)

const pctOf = (v) => (typeof v === 'number' && Number.isFinite(v) ? `= ${(v * 100).toFixed(2)}%` : '')
</script>

<style scoped>
.trade { display: flex; flex-direction: column; gap: 14px; border: 0; padding: 0; margin: 0; min-width: 0; }
.trade-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px 16px; }
.with-pct { display: flex; align-items: center; gap: 8px; }
.pct { font-family: var(--jd-mono); font-size: 12px; color: var(--jd-text-muted); white-space: nowrap; min-width: 64px; }
.hint { margin-top: 3px; }
.radios { display: flex; gap: 16px; flex-wrap: wrap; padding-top: 8px; font-size: 13px; }
.radios label { display: flex; align-items: center; gap: 6px; cursor: pointer; }
.radios .off { opacity: 0.5; cursor: not-allowed; }
.field-error { color: var(--jd-red); font-size: 12px; margin: 4px 0 0; }
</style>
