/**
 * Lightweight global toast — scope-skinned replacement for PrimeVue useToast.
 * API-compatible: toast.add({ severity, summary, detail, life }).
 * severity: 'success' | 'info' | 'warn' | 'error'
 */
import { reactive } from 'vue'

const state = reactive({ items: [] })
let seq = 0

const remove = (id) => {
  const i = state.items.findIndex((t) => t.id === id)
  if (i > -1) state.items.splice(i, 1)
}

const add = ({ severity = 'info', summary = '', detail = '', life = 3000 } = {}) => {
  const id = ++seq
  state.items.push({ id, severity, summary, detail })
  if (life > 0) setTimeout(() => remove(id), life)
  return id
}

// Convenience wrappers — several views call toast.success/error directly
// (QA finding #7/#8: calling a missing method threw inside the try block and
// silently ate both the toast and the post-trade refresh).
const success = (detail = '', summary = 'Done') =>
  add({ severity: 'success', summary, detail })
const error = (detail = '', summary = 'Error') =>
  add({ severity: 'error', summary, detail, life: 6000 })
const info = (detail = '', summary = '') =>
  add({ severity: 'info', summary, detail })
const warn = (detail = '', summary = '') =>
  add({ severity: 'warn', summary, detail, life: 5000 })

export function useToast() {
  return { add, remove, success, error, info, warn }
}

// consumed only by ToastHost
export function useToastState() {
  return { state, remove }
}
