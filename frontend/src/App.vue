<template>
  <div v-if="showPreloader" id="preloader" aria-label="Loading application">
    <div id="status">
      <div class="judia-spinner" aria-hidden="true"></div>
    </div>
  </div>
  <router-view />
  <ToastHost />
</template>

<script setup>
import { onMounted, ref } from 'vue'
import ToastHost from '@/components/common/ToastHost.vue'

const showPreloader = ref(document.documentElement.getAttribute('data-preloader') === 'enable')

onMounted(() => {
  window.setTimeout(() => {
    showPreloader.value = false
  }, 450)
})
</script>

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: 'Space Grotesk', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

#app {
  min-height: 100vh;
}
</style>
