<template>
  <div class="group-selector">
    <label class="jd-label" for="strategy-group-select">Strategy group</label>
    <select v-if="store.groups.length" id="strategy-group-select" class="jd-input jd-select"
            style="width:auto;min-width:220px" :value="store.selectedId"
            @change="store.select($event.target.value)">
      <option v-for="g in store.groups" :key="g.id" :value="g.id">
        {{ optionLabel(g) }}
      </option>
    </select>
    <span v-else class="jd-sub">
      {{ store.loading ? 'Loading…' : store.error ? 'Could not load strategy groups' : 'No strategy groups yet' }}
    </span>
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import { useStrategyGroupsStore } from '@/stores/strategyGroups'

const store = useStrategyGroupsStore()

const optionLabel = g =>
  `${g.name} · ${g.strategy_key}${g.status === 'active' ? '' : ` (${g.status})`}`

onMounted(() => store.ensureLoaded())
</script>
