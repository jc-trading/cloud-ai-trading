<!-- Global toast host. Mount once (App.vue). Renders toasts from useToast(). -->
<template>
  <Teleport to="body">
    <div class="jd-toast-host" aria-live="polite" aria-atomic="false">
      <TransitionGroup name="jd-toast">
        <div
          v-for="t in state.items" :key="t.id"
          class="jd-toast" :class="t.severity"
          role="status"
          @click="remove(t.id)"
        >
          <i class="pi" :class="icon(t.severity)"></i>
          <div class="jd-toast-body">
            <div v-if="t.summary" class="jd-toast-summary">{{ t.summary }}</div>
            <div v-if="t.detail" class="jd-toast-detail">{{ t.detail }}</div>
          </div>
          <button class="jd-toast-close" aria-label="Dismiss" @click.stop="remove(t.id)">
            <i class="pi pi-times"></i>
          </button>
        </div>
      </TransitionGroup>
    </div>
  </Teleport>
</template>

<script setup>
import { useToastState } from '@/composables/useToast'
const { state, remove } = useToastState()
const icon = (s) => ({
  success: 'pi-check-circle',
  error: 'pi-times-circle',
  warn: 'pi-exclamation-triangle',
  info: 'pi-info-circle',
}[s] || 'pi-info-circle')
</script>

<style scoped>
.jd-toast-host {
  position: fixed; top: 18px; right: 18px; z-index: 9000;
  display: flex; flex-direction: column; gap: 10px; max-width: 360px;
}
.jd-toast {
  display: flex; align-items: flex-start; gap: 11px;
  background: linear-gradient(160deg, var(--jd-card-2), var(--jd-card));
  border: 1px solid var(--jd-border); border-left-width: 3px;
  border-radius: 11px; padding: 13px 14px; cursor: pointer;
  box-shadow: var(--jd-shadow-modal); backdrop-filter: blur(6px);
}
.jd-toast > .pi { font-size: 16px; margin-top: 1px; }
.jd-toast.success { border-left-color: var(--jd-green); }
.jd-toast.success > .pi { color: var(--jd-green); }
.jd-toast.error { border-left-color: var(--jd-red); }
.jd-toast.error > .pi { color: var(--jd-red); }
.jd-toast.warn { border-left-color: var(--jd-yellow); }
.jd-toast.warn > .pi { color: var(--jd-yellow); }
.jd-toast.info { border-left-color: var(--jd-cyan); }
.jd-toast.info > .pi { color: var(--jd-cyan); }
.jd-toast-body { flex: 1; min-width: 0; }
.jd-toast-summary { font-size: 13px; font-weight: 600; color: var(--jd-text); }
.jd-toast-detail { font-size: 12px; color: var(--jd-text-muted); margin-top: 2px; word-break: break-word; }
.jd-toast-close { background: none; border: none; color: var(--jd-text-faint); cursor: pointer; padding: 0 2px; font-size: 12px; }
.jd-toast-close:hover { color: var(--jd-text); }

.jd-toast-enter-active, .jd-toast-leave-active { transition: all 0.25s cubic-bezier(0.4,0,0.2,1); }
.jd-toast-enter-from { opacity: 0; transform: translateX(20px); }
.jd-toast-leave-to { opacity: 0; transform: translateX(20px); }
@media (prefers-reduced-motion: reduce) { .jd-toast-enter-active, .jd-toast-leave-active { transition: none; } }
</style>
