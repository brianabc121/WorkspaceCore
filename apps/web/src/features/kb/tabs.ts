import { ref, watch } from 'vue'
import { useAuthStore } from '../../stores/auth'

// 打开的页面（T-0099，效果图的标签栏）：最近打开的笔记，记在本标签页的 sessionStorage 里（最多 6 个）。
// 只是导航上的方便，丢了也没关系；读写失败时只保存在内存里。
export type OpenTab = { id: string; title: string }
const MAX = 6
const tabs = ref<OpenTab[]>([])
let loadedKey = ''

function storageKey() {
  const auth = useAuthStore()
  return `kb-tabs:${auth.user?.id ?? ''}:${auth.workspaceId ?? ''}`
}
function load(key: string) {
  if (key === loadedKey) return
  loadedKey = key
  try {
    const raw = JSON.parse(sessionStorage.getItem(key) ?? '[]') as unknown
    tabs.value = Array.isArray(raw) ? raw.filter((t): t is OpenTab => typeof t?.id === 'string' && typeof t?.title === 'string').slice(0, MAX) : []
  } catch { tabs.value = [] }
}
watch(tabs, (value) => { try { if (loadedKey) sessionStorage.setItem(loadedKey, JSON.stringify(value)) } catch { /* 只保存在内存里 */ } }, { deep: true })

export function useOpenTabs() {
  load(storageKey())
  return {
    tabs,
    open(tab: OpenTab) {
      const rest = tabs.value.filter(t => t.id !== tab.id)
      const index = tabs.value.findIndex(t => t.id === tab.id)
      tabs.value = index >= 0 ? tabs.value.map(t => t.id === tab.id ? tab : t) : [tab, ...rest].slice(0, MAX)
    },
    close(id: string) { tabs.value = tabs.value.filter(t => t.id !== id) },
  }
}
