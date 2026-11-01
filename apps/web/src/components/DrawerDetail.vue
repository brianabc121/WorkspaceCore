<script setup lang="ts">
import { computed, nextTick, onUnmounted, watch } from 'vue'
import { useDrawerStore } from '../stores/drawer'

// 把页面上的详情（例如任务的新建 / 编辑）放进右侧抽屉的“详情”页（规范 §8.1、§8.4 Task Drawer）。
// 内容用 Teleport 渲染到抽屉里，仍属于页面组件：事件、状态都照常。
const props = defineProps<{ title: string; busy?: boolean }>()
const emit = defineEmits<{ close: [] }>()
const drawer = useDrawerStore()
const token = Symbol('drawer-detail')
const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
drawer.showDetail({ token, title: props.title, busy: !!props.busy, close: () => emit('close') })
watch(() => [props.title, !!props.busy] as const, ([title, busy]) => drawer.updateDetail(token, { title, busy }))
const target = computed(() => (drawer.detail?.token === token ? drawer.target : null))
// 把焦点移进详情：内容可能要等接口返回才渲染（例如任务详情），所以等第一个输入框出现再移；
// 用户这期间已经在别处操作（焦点不在打开它的按钮或 body 上）就不抢焦点
let observer: MutationObserver | null = null
function stopObserving() { observer?.disconnect(); observer = null }
function focusInto(element: HTMLElement) {
  if (element.contains(document.activeElement)) return true
  const active = document.activeElement
  if (active && active !== document.body && active !== previous) return true
  const field = element.querySelector<HTMLElement>('[autofocus], input:not([type="hidden"]), textarea, select')
  field?.focus()
  return !!field
}
watch(target, async (element) => {
  stopObserving()
  if (!element) return
  await nextTick()
  if (focusInto(element)) return
  observer = new MutationObserver(() => { if (focusInto(element)) stopObserving() })
  observer.observe(element, { childList: true, subtree: true })
}, { immediate: true })
onUnmounted(() => {
  stopObserving()
  drawer.hideDetail(token)
  if (previous?.isConnected) previous.focus()
})
</script>

<template>
  <Teleport v-if="target" :to="target"><slot /></Teleport>
</template>
