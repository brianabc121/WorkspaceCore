import { nextTick, onScopeDispose, ref, watch } from 'vue'

// 消息页里的小弹出菜单（新建、更多、消息操作、@ 成员）：点外面或按 Esc 关闭，关闭后焦点回到触发按钮；
// 打开时焦点放到第一项，上下方向键在各项之间移动。root 绑在包住按钮和菜单的元素上。
export function usePopover() {
  const open = ref(false), root = ref<HTMLElement | null>(null)
  const trigger = () => root.value?.querySelector<HTMLElement>('[aria-haspopup]') ?? null
  const items = () => [...(root.value?.querySelectorAll<HTMLElement>('[role="menuitem"]:not(:disabled)') ?? [])]
  function outside(event: Event) { if (root.value && !root.value.contains(event.target as Node)) open.value = false }
  function keys(event: KeyboardEvent) {
    if (event.key === 'Escape') { event.stopPropagation(); open.value = false; trigger()?.focus(); return }
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return
    const list = items(), index = list.indexOf(document.activeElement as HTMLElement)
    if (!list.length) return
    event.preventDefault()
    list[(index + (event.key === 'ArrowDown' ? 1 : -1) + list.length) % list.length].focus()
  }
  const detach = () => { document.removeEventListener('pointerdown', outside, true); root.value?.removeEventListener('keydown', keys) }
  watch(open, async (value) => {
    detach()
    if (!value) return
    document.addEventListener('pointerdown', outside, true)
    root.value?.addEventListener('keydown', keys)
    await nextTick()
    items()[0]?.focus()
  })
  onScopeDispose(detach)
  return { open, root, toggle: () => { open.value = !open.value }, close: () => { open.value = false } }
}
