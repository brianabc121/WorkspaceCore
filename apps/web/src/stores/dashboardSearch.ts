import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import { useAuthStore } from './auth'
import type { Schema } from '../services/api'
export const useDashboardSearch = defineStore('dashboard-search', () => {
  const widgets = ref<Schema['WidgetOut'][]>([])
  const auth = useAuthStore()
  watch(() => `${auth.user?.id}:${auth.workspaceId}`, () => { widgets.value = [] }, { flush: 'sync' })
  return { widgets }
})
