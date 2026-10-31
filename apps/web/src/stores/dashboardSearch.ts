import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import { useAuthStore } from './auth'
import type { Schema } from '../services/api'
export const useDashboardSearch = defineStore('dashboard-search', () => {
  const widgets = ref<Schema['WidgetOut'][]>([]), denied = ref(false), denialRevision = ref(0)
  const auth = useAuthStore()
  watch(() => auth.user?.id + ':' + auth.workspaceId, () => { widgets.value = []; denied.value = false; denialRevision.value++ }, { flush: 'sync' })
  function denyScope() { widgets.value = []; denied.value = true; denialRevision.value++ }
  return { widgets, denied, denialRevision, denyScope }
})
