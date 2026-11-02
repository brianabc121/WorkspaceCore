import { ref } from 'vue'

// 侧栏分组的折叠状态，记在本机浏览器里（T-0083）。第一次打开时只有“管理”是折叠的。
// 存储不可用（隐私模式、被禁用）时只在内存里记住。
const KEY = 'workspace-nav-collapsed'
const DEFAULT = ['管理']

function load(): string[] {
  try {
    const saved = localStorage.getItem(KEY)
    if (saved === null) return [...DEFAULT]
    const parsed: unknown = JSON.parse(saved)
    return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === 'string') : [...DEFAULT]
  } catch {
    return [...DEFAULT]
  }
}

export function useCollapsedSections() {
  const collapsed = ref<string[]>(load())
  function toggle(section: string) {
    collapsed.value = collapsed.value.includes(section) ? collapsed.value.filter((item) => item !== section) : [...collapsed.value, section]
    try { localStorage.setItem(KEY, JSON.stringify(collapsed.value)) } catch { /* 只在内存里记住 */ }
  }
  return { collapsed, toggle }
}
