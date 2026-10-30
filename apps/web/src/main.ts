import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import App from './App.vue'
import { createAppRouter } from './app/router'
import './style.css'

const pinia = createPinia()
const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } })
createApp(App)
  .use(pinia)
  .use(createAppRouter(pinia, undefined, queryClient))
  .use(VueQueryPlugin, { queryClient })
  .mount('#app')
