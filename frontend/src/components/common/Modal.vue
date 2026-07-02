<!--
  Modal — scope-skinned dialog. Replaces PrimeVue Dialog.
  Usage: <Modal v-model="show" title="…"> body <template #footer>…</template> </Modal>
-->
<template>
  <Teleport to="body">
    <Transition name="jd-modal">
      <div v-if="modelValue" class="jd-modal-backdrop" @click.self="close">
        <div class="jd-modal" role="dialog" aria-modal="true" :style="{ maxWidth: width }">
          <div class="jd-modal-head">
            <h3 class="jd-modal-title">{{ title }}</h3>
            <button class="jd-modal-x" aria-label="Close" @click="close"><i class="pi pi-times"></i></button>
          </div>
          <div class="jd-modal-body"><slot /></div>
          <div v-if="$slots.footer" class="jd-modal-foot"><slot name="footer" /></div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import { onMounted, onBeforeUnmount } from 'vue'
const props = defineProps({
  modelValue: { type: Boolean, default: false },
  title: { type: String, default: '' },
  width: { type: String, default: '480px' },
})
const emit = defineEmits(['update:modelValue'])
const close = () => emit('update:modelValue', false)
const onKey = (e) => { if (e.key === 'Escape' && props.modelValue) close() }
onMounted(() => document.addEventListener('keydown', onKey))
onBeforeUnmount(() => document.removeEventListener('keydown', onKey))
</script>

<style scoped>
.jd-modal-backdrop {
  position: fixed; inset: 0; z-index: 8000;
  background: rgba(2, 4, 10, 0.72); backdrop-filter: blur(4px);
  display: flex; align-items: center; justify-content: center; padding: 24px;
}
.jd-modal {
  width: 100%;
  background: linear-gradient(160deg, var(--jd-card-2), var(--jd-card));
  border: 1px solid var(--jd-border); border-radius: 16px;
  box-shadow: var(--jd-shadow-modal); overflow: hidden;
}
.jd-modal-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 16px 20px; border-bottom: 1px solid var(--jd-border);
}
.jd-modal-title { margin: 0; font-size: 15px; font-weight: 600; color: var(--jd-text); }
.jd-modal-x { background: none; border: none; color: var(--jd-text-muted); cursor: pointer; font-size: 14px; }
.jd-modal-x:hover { color: var(--jd-text); }
.jd-modal-body { padding: 20px; }
.jd-modal-foot { padding: 14px 20px; border-top: 1px solid var(--jd-border); display: flex; justify-content: flex-end; gap: 10px; }

.jd-modal-enter-active, .jd-modal-leave-active { transition: opacity 0.2s ease; }
.jd-modal-enter-from, .jd-modal-leave-to { opacity: 0; }
@media (prefers-reduced-motion: reduce) { .jd-modal-enter-active, .jd-modal-leave-active { transition: none; } }
</style>
