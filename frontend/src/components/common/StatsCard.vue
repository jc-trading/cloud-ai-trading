<template>
  <div class="bg-gray-900 rounded-xl border border-gray-800 p-5">
    <div class="flex items-center justify-between mb-3">
      <span class="text-sm text-gray-400">{{ title }}</span>
      <div class="w-10 h-10 rounded-lg flex items-center justify-center"
        :class="`bg-${color}-600/20`">
        <i :class="['pi', icon, `text-${color}-400`]"></i>
      </div>
    </div>
    <div class="text-2xl font-bold text-white">{{ value }}</div>
    <div class="text-xs text-gray-500 mt-1">{{ subtitle }}</div>
  </div>
</template>

<script setup>
defineProps({
  title: String,
  value: String,
  icon: String,
  color: { type: String, default: 'blue' },
  subtitle: String,
})
</script>
