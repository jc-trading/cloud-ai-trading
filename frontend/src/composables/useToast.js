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

export function useToast() {
  return { add, remove }
}

// consumed only by ToastHost
export function useToastState() {
  return { state, remove }
}
