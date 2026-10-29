import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { VueQueryPlugin } from '@tanstack/vue-query'
import App from './App.vue'
import { createAppRouter } from './app/router'
import './style.css'

createApp(App)
  .use(createPinia())
  .use(createAppRouter())
  .use(VueQueryPlugin, { queryClientConfig: { defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } } })
  .mount('#app')
