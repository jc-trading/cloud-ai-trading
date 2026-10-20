<template>
  <fieldset class="screener" :disabled="disabled" @input="onUserInput" @change="onUserInput">
    <p v-if="errors.screener" class="field-error">{{ errors.screener }}</p>
    <div class="screener-head">
      <label class="jd-toggle">
        <input v-model="legacy" type="checkbox" />
        <span class="jd-toggle-slider"></span>
      </label>
      <span class="jd-label" style="margin:0">Legacy v1 pool (no screener)</span>
      <button v-if="!legacy" type="button" class="jd-btn jd-btn-ghost jd-btn-sm" style="margin-left:auto" @click="applyDefault">
        Default
      </button>
    </div>

    <template v-if="!legacy">
      <div v-for="(rule, i) in rules" :key="i" class="rule">
        <div class="rule-row">
          <select v-model="rule.field" class="jd-input jd-select" @change="rule.window = ''">
            <option v-for="f in fieldNames" :key="f" :value="f">{{ f }}</option>
          </select>
          <select v-model="rule.op" class="jd-input jd-select">
            <option v-for="op in vocab.ops" :key="op" :value="op">{{ op }}</option>
          </select>
          <input v-model.number="rule.value" class="jd-input jd-mono" type="number" step="any" placeholder="value" />
          <input v-if="isWindowed(rule.field)" v-model.number="rule.window" class="jd-input jd-mono" type="number" step="1"
                 :min="vocab.fields[rule.field][0]" :max="vocab.fields[rule.field][1]"
                 :placeholder="`window ${vocab.fields[rule.field][0]}–${vocab.fields[rule.field][1]}`" />
          <span v-else></span>
          <select v-model="rule.on_null" class="jd-input jd-select" title="When the value is missing">
            <option value="fail">null fails</option>
            <option value="pass">null passes</option>
          </select>
          <button type="button" class="jd-btn jd-btn-danger jd-btn-sm" title="Remove rule" @click="removeRule(i)">
            <i class="pi pi-trash"></i>
          </button>
        </div>
        <div class="jd-src">{{ boundsText(rule.field) }}</div>
        <p v-if="errors[`screener.rules.${i}`]" class="field-error">{{ errors[`screener.rules.${i}`] }}</p>
      </div>
      <button type="button" class="jd-btn jd-btn-ghost jd-btn-sm" :disabled="rules.length >= vocab.max_rules" @click="addRule">
        <i class="pi pi-plus"></i> Add rule
      </button>

      <div class="lists">
        <div>
          <label class="jd-label" for="screener-include">Include (always in the pool)</label>
          <textarea id="screener-include" v-model="includeText" class="jd-input jd-mono" rows="2"
                    placeholder="AAPL, MSFT" @blur="upperOnBlur('include')"></textarea>
          <p v-if="errors['screener.include']" class="field-error">{{ errors['screener.include'] }}</p>
        </div>
        <div>
          <label class="jd-label" for="screener-exclude">Exclude (never in the pool)</label>
          <textarea id="screener-exclude" v-model="excludeText" class="jd-input jd-mono" rows="2"
                    placeholder="TSLA" @blur="upperOnBlur('exclude')"></textarea>
          <p v-if="errors['screener.exclude']" class="field-error">{{ errors['screener.exclude'] }}</p>
        </div>
      </div>

      <div>
        <div class="jd-label">Asset types</div>
        <div class="asset-types">
          <label v-for="t in vocab.asset_types" :key="t" class="asset-type">
            <input v-model="assetTypes" type="checkbox" :value="t" /> {{ t }}
          </label>
        </div>
        <p v-if="errors['screener.asset_types']" class="field-error">{{ errors['screener.asset_types'] }}</p>
      </div>
    </template>
  </fieldset>
</template>

<script setup>
import { ref, computed, watch } from 'vue'

const props = defineProps({
  vocab: { type: Object, required: true },
  modelValue: { type: Object, required: true },
  errors: { type: Object, default: () => ({}) },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'touched'])

const legacy = ref(false)
const rules = ref([])
const includeText = ref('')
const excludeText = ref('')
const assetTypes = ref([])
let lastEmitted = null

const fieldNames = computed(() => Object.keys(props.vocab.fields))
const isWindowed = (field) => props.vocab.fields[field] != null

const boundsText = (field) => {
  const [lo, hi] = props.vocab.value_bounds[field] || []
  const value = hi == null ? `value ≥ ${lo}` : `value [${lo}, ${hi}]`
  if (!isWindowed(field)) return value
  const [wlo, whi] = props.vocab.fields[field]
  return `${value} · window [${wlo}, ${whi}]`
}

function parse(m) {
  legacy.value = !m || Object.keys(m).length === 0
  rules.value = (m?.rules || []).map((r) => ({
    field: r.field, op: r.op, value: r.value ?? '', window: r.window ?? '', on_null: r.on_null || 'fail',
  }))
  includeText.value = (m?.include || []).join(', ')
  excludeText.value = (m?.exclude || []).join(', ')
  assetTypes.value = m?.asset_types ? [...m.asset_types] : [...props.vocab.asset_types]
}

const splitSymbols = (text) => text.split(/[\s,]+/).filter((s) => s !== '')

function build() {
  const out = {
    rules: rules.value.map((r) => {
      const rule = { field: r.field, op: r.op, value: r.value }
      if (isWindowed(r.field) && r.window !== '') rule.window = r.window
      if (r.on_null === 'pass') rule.on_null = 'pass'
      return rule
    }),
  }
  const include = splitSymbols(includeText.value)
  const exclude = splitSymbols(excludeText.value)
  if (include.length) out.include = include
  if (exclude.length) out.exclude = exclude
  const types = props.vocab.asset_types.filter((t) => assetTypes.value.includes(t))
  if (JSON.stringify(types) !== JSON.stringify(props.vocab.asset_types)) out.asset_types = types
  return out
}

function emitModel() {
  lastEmitted = legacy.value ? {} : build()
  emit('update:modelValue', lastEmitted)
}

function onUserInput() {
  emit('touched')
  emitModel()
}

function upperOnBlur(list) {
  const text = list === 'include' ? includeText : excludeText
  const upper = text.value.toUpperCase()
  if (upper === text.value) return
  text.value = upper
  onUserInput()
}

function applyDefault() {
  parse(JSON.parse(JSON.stringify(props.vocab.default)))
  onUserInput()
}

function addRule() {
  rules.value.push({ field: fieldNames.value[0], op: props.vocab.ops[0], value: '', window: '', on_null: 'fail' })
  onUserInput()
}

function removeRule(i) {
  rules.value.splice(i, 1)
  onUserInput()
}

parse(props.modelValue)
watch(() => props.modelValue, (v) => {
  if (JSON.stringify(v) !== JSON.stringify(lastEmitted)) parse(v)
})
</script>

<style scoped>
.screener { display: flex; flex-direction: column; gap: 12px; border: 0; padding: 0; margin: 0; min-width: 0; }
.screener-head { display: flex; align-items: center; gap: 10px; }
.rule-row { display: grid; grid-template-columns: 1.3fr 0.7fr 1.2fr 1fr 1fr auto; gap: 8px; align-items: center; }
.rule .jd-src { margin-top: 3px; }
.lists { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.asset-types { display: flex; gap: 16px; flex-wrap: wrap; }
.asset-type { display: flex; align-items: center; gap: 6px; font-size: 13px; cursor: pointer; }
.field-error { color: var(--jd-red); font-size: 12px; margin: 4px 0 0; }
@media (max-width: 720px) {
  .rule-row, .lists { grid-template-columns: 1fr; }
}
</style>
