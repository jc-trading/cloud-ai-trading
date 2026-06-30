<template>
  <div v-if="showPreloader" id="preloader" aria-label="Loading application">
    <div id="status">
      <div class="judia-spinner" aria-hidden="true"></div>
    </div>
  </div>
  <router-view />
</template>

<script setup>
import { onMounted, ref } from 'vue'

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
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 'Oxygen',
    'Ubuntu', 'Cantarell', 'Fira Sans', 'Droid Sans', 'Helvetica Neue',
    sans-serif;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

#app {
  min-height: 100vh;
}
</style>
