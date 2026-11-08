import { ref, watch } from 'vue'
import { defineStore } from 'pinia'
import { useAuthStore } from './auth'
import { dataOf, type Schema } from '../services/api'

// 侧栏布局（T-0161，GET /api/v1/navigation）：自定义分组、页面挪分组、零代码页面。
// 登录、切换工作空间时重新读取；保存后后端发 navigation 实时事件，useWorkspaceEvents 让这里重新读。
// 读不到（请求失败）时 layout 为 null，侧栏就是默认的样子。放在 store 里而不是查询缓存里：登出后不留下任何查询。
export const useNavigationStore = defineStore('navigation', () => {
  const auth = useAuthStore()
  const layout = ref<Schema['NavigationOut'] | null>(null)
  const loaded = ref(false)
  const error = ref<unknown>(null)
  let identity = 0, latest = 0

  /** 重新读取；返回读到的布局（失败或已过期时为 null） */
  async function refresh(): Promise<Schema['NavigationOut'] | null> {
    const scope = auth.workspaceId, context = identity, request = ++latest
    if (!auth.user || !scope) return null
    const current = () => context === identity && request === latest
    try {
      const data = dataOf(await auth.api.GET('/api/v1/navigation', { params: { header: { 'X-Workspace-Id': scope } } }))
      if (!current()) return null
      layout.value = data; error.value = null
      return data
    } catch (cause) {
      if (current()) error.value = cause
      return null
    } finally {
      if (current()) loaded.value = true
    }
  }
  /** 设置页保存成功后直接用返回的布局，侧栏立刻变化 */
  function apply(value: Schema['NavigationOut']) {
    latest++
    layout.value = value; error.value = null; loaded.value = true
  }
  watch(() => `${auth.user?.id}:${auth.workspaceId}`, () => {
    identity++; layout.value = null; loaded.value = false; error.value = null
    void refresh()
  }, { flush: 'sync', immediate: true })
  return { layout, loaded, error, refresh, apply }
})
