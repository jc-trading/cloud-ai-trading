<template>
  <router-link :to="to" :class="['jd-nav-item', { active: isActive }]">
    <span class="nav-icon">
      <i :class="['pi', icon]"></i>
    </span>
    <span class="truncate">{{ label }}</span>
  </router-link>
</template>

<script setup>
defineProps({
  to: { type: String, required: true },
  icon: { type: String, required: true },
  label: { type: String, required: true },
  isActive: { type: Boolean, default: false }
})
</script>

<style scoped>
a { text-decoration: none; }
</style>
