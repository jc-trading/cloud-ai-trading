import { createApp } from 'vue'
import { createPinia } from 'pinia'
import './assets/main.css'

import App from './App.vue'
import router from './router/index.js'

const judiaPreset = {
  'data-layout': 'vertical',
  'data-bs-theme': 'dark',
  'data-card-layout': 'borderless',
  'data-layout-width': 'boxed',
  'data-layout-position': 'scrollable',
  'data-sidebar-size': 'lg',
  'data-sidebar': 'dark',
  'data-topbar': 'dark',
  'data-topbar-image': 'pattern-1',
  'data-preloader': 'enable',
}

Object.entries(judiaPreset).forEach(([key, value]) => {
  document.documentElement.setAttribute(key, value)
})

const app = createApp(App)

app.use(createPinia())
app.use(router)

app.mount('#app')
