import { computed, ref, watch } from 'vue'
import { defineStore } from 'pinia'
import { useAuthStore } from './auth'
import { dataOf, type Schema } from '../services/api'

// 插拔式模块在当前工作空间的启用状态（T-0159，GET /api/v1/modules）。
// 登录、切换工作空间时重新读取；实时事件的 modules 主题、接口返回 MODULE_DISABLED 时也重新读取。
// 还没读到、读取失败或后端不认识的模块按“启用”处理，免得侧栏闪一下；真正的拦截在后端（停用的模块接口返回 404 MODULE_DISABLED）。
export const useModulesStore = defineStore('modules', () => {
  const auth = useAuthStore()
  const items = ref<Schema['ModuleOut'][]>([])
  const loaded = ref(false)
  const error = ref<unknown>(null)
  let identity = 0, latest = 0
  let pending: Promise<void> | null = null
  const byKey = computed(() => new Map(items.value.map((item) => [item.key, item])))

  function isEnabled(key: string | null | undefined) {
    return !key || (byKey.value.get(key)?.enabled ?? true)
  }
  function refresh(): Promise<void> {
    const user = auth.user?.id, scope = auth.workspaceId, context = identity, request = ++latest
    if (!user || !scope) return Promise.resolve()
    const current = (async () => {
      try {
        const data = dataOf(await auth.api.GET('/api/v1/modules', { params: { header: { 'X-Workspace-Id': scope } } }))
        if (context === identity && request === latest) { items.value = data.items; error.value = null }
      } catch (cause) {
        if (context === identity && request === latest) error.value = cause
      } finally {
        if (context === identity && request === latest) { loaded.value = true; pending = null }
      }
    })()
    pending = current
    return current
  }
  /** 路由守卫用：等当前工作空间的启用状态读完（成功或失败）再打开模块页面 */
  function ready(): Promise<void> {
    if (loaded.value) return Promise.resolve()
    return pending ?? refresh()
  }
  /** 应用中心启用 / 停用后，先用返回的结果更新，侧栏立刻变化 */
  function apply(module: Schema['ModuleOut']) {
    items.value = items.value.map((item) => (item.key === module.key ? module : item))
  }
  watch(() => `${auth.user?.id}:${auth.workspaceId}`, () => {
    identity++; items.value = []; loaded.value = false; error.value = null; pending = null
    void refresh()
  }, { flush: 'sync', immediate: true })
  watch(() => auth.moduleDisabledSignal, () => { void refresh() })
  return { items, loaded, error, isEnabled, refresh, ready, apply }
})
