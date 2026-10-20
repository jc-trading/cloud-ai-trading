<template>
  <div class="params-form">
    <p v-if="errors.params" class="field-error">{{ errors.params }}</p>
    <p v-if="crossRules.length" class="jd-src">{{ crossRulesText }}</p>
    <fieldset v-for="(fields, section) in schema" :key="section" class="params-section" :disabled="disabled">
      <legend class="jd-h2">{{ section }}</legend>
      <div class="params-grid">
        <div v-for="(spec, field) in fields" :key="field" class="params-field">
          <label class="jd-label" :for="`param-${section}-${field}`">{{ field }}</label>
          <input
            :id="`param-${section}-${field}`"
            class="jd-input jd-mono"
            type="number"
            :step="spec.type === 'int' ? 1 : 'any'"
            :min="spec.min"
            :max="spec.max"
            :placeholder="placeholderOf(section, field)"
            v-model.number="local[section][field]"
          />
          <div class="jd-src">[{{ spec.min }}, {{ spec.max }}]</div>
          <p v-if="errors[`params.${section}.${field}`]" class="field-error">{{ errors[`params.${section}.${field}`] }}</p>
        </div>
      </div>
    </fieldset>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'

const props = defineProps({
  schema: { type: Object, required: true },
  crossRules: { type: Array, default: () => [] },
  modelValue: { type: Object, required: true },
  base: { type: Object, default: () => ({}) },
  errors: { type: Object, default: () => ({}) },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue'])

const crossRulesText = computed(() =>
  props.crossRules.map((r) => `${r.left} ${r.op} ${r.right}`).join(' · '))

const placeholderOf = (section, field) => {
  const v = props.base?.[section]?.[field]
  return v == null ? '' : String(v)
}

const withSections = (m) => {
  const out = JSON.parse(JSON.stringify(m || {}))
  for (const section of Object.keys(props.schema)) out[section] = out[section] || {}
  return out
}

const local = ref(withSections(props.modelValue))
watch(() => props.modelValue, (v) => {
  if (JSON.stringify(v) !== JSON.stringify(local.value)) local.value = withSections(v)
}, { deep: true })
watch(local, (v) => emit('update:modelValue', JSON.parse(JSON.stringify(v))), { deep: true })
</script>

<style scoped>
.params-form { display: flex; flex-direction: column; gap: 14px; }
.params-section { border: 1px solid var(--jd-line-2); border-radius: 10px; padding: 12px 16px 4px; margin: 0; }
.params-section legend { padding: 0 6px; margin: 0; }
.params-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 4px 14px; }
.params-field { margin-bottom: 10px; }
.params-field .jd-src { margin-top: 3px; }
.field-error { color: var(--jd-red); font-size: 12px; margin: 4px 0 0; }
</style>
