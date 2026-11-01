import { defineStore } from 'pinia'
import { ref, shallowRef } from 'vue'

// 右侧“AI / 详情”抽屉（规范 §8.1）。打开与否记在浏览器里（只是个人偏好）；
// 页面用 DrawerDetail 把详情（例如 Task Drawer）放进抽屉的“详情”页，关掉详情后恢复抽屉原来的状态。
export type DrawerTab = 'ai' | 'detail'
export type DrawerDetail = { token: symbol; title: string; busy: boolean; close: () => void }
const KEY = 'workspace-drawer-open'

function readOpen() {
  try { return localStorage.getItem(KEY) === '1' } catch { return false }
}

export const useDrawerStore = defineStore('drawer', () => {
  const open = ref(readOpen())
  const tab = ref<DrawerTab>('ai')
  const detail = shallowRef<DrawerDetail | null>(null)
  const target = shallowRef<HTMLElement | null>(null)
  // 详情打开之前抽屉的状态；详情关闭后恢复
  let restore: { open: boolean; tab: DrawerTab } | null = null

  function setOpen(value: boolean) {
    open.value = value
    try { localStorage.setItem(KEY, value ? '1' : '0') } catch { /* 只在内存里记住 */ }
  }
  /** 收起抽屉；有正在提交的详情时不收起，返回 false。 */
  function requestClose() {
    const current = detail.value
    if (current?.busy) return false
    if (current) {
      restore = { open: false, tab: 'ai' }
      current.close()
    }
    setOpen(false)
    return true
  }
  function toggle() {
    if (open.value) requestClose()
    else setOpen(true)
  }
  function showDetail(value: DrawerDetail) {
    if (!detail.value) restore = { open: open.value, tab: tab.value }
    detail.value = value
    tab.value = 'detail'
    open.value = true
  }
  function updateDetail(token: symbol, patch: Partial<Pick<DrawerDetail, 'title' | 'busy'>>) {
    if (detail.value?.token === token) detail.value = { ...detail.value, ...patch }
  }
  function hideDetail(token: symbol) {
    if (detail.value?.token !== token) return
    detail.value = null
    const previous = restore ?? { open: open.value, tab: 'ai' as DrawerTab }
    restore = null
    open.value = previous.open
    tab.value = previous.tab === 'detail' ? 'ai' : previous.tab
  }
  /** 关闭详情（页面收到 close 事件后卸载 DrawerDetail）；正在提交时不关闭。 */
  function closeDetail() {
    const current = detail.value
    if (current && !current.busy) current.close()
  }
  function setTarget(element: HTMLElement | null) { target.value = element }

  return { open, tab, detail, target, setOpen, requestClose, toggle, showDetail, updateDetail, hideDetail, closeDetail, setTarget }
})
