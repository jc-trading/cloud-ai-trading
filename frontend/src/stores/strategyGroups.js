import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { listStrategyGroups } from '../api/strategyGroups'

const STORAGE_KEY = 'cat.strategyGroupId'

function readStored() {
  try {
    return localStorage.getItem(STORAGE_KEY)
  } catch {
    return null
  }
}

function writeStored(id) {
  try {
    localStorage.setItem(STORAGE_KEY, id)
  } catch { /* private mode / quota — the choice just doesn't survive a reload */ }
}

export const useStrategyGroupsStore = defineStore('strategyGroups', () => {
  const groups = ref([])
  const selectedId = ref(null)
  const loaded = ref(false)
  const loading = ref(false)
  const error = ref(null)
  let inflight = null

  const selected = computed(() => groups.value.find(g => g.id === selectedId.value) || null)

  const fetchGroups = async () => {
    loading.value = true
    error.value = null
    try {
      const { data } = await listStrategyGroups()
      groups.value = data
      const ids = data.map(g => g.id)
      const wanted = selectedId.value ?? readStored()
      selectedId.value = ids.includes(wanted) ? wanted : (data[0]?.id ?? null)
      loaded.value = true
    } catch (e) {
      const detail = e?.response?.data?.detail
      error.value = typeof detail === 'string' ? detail : 'Failed to load strategy groups'
    } finally {
      loading.value = false
    }
  }

  const ensureLoaded = (force = false) => {
    if (loaded.value && !force) return Promise.resolve()
    if (!inflight) inflight = fetchGroups().finally(() => { inflight = null })
    return inflight
  }

  const select = (id) => {
    selectedId.value = id
    writeStored(id)
  }

  return {
    groups,
    selectedId,
    loaded,
    loading,
    error,
    selected,
    ensureLoaded,
    select,
  }
})
