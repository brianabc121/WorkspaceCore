<script setup lang="ts">
import { watch } from 'vue'
import { useAuthStore } from '../stores/auth'
import { useDrawerStore } from '../stores/drawer'
import { DRAWER_DOCKED_QUERY, useMediaQuery } from '../composables/useMediaQuery'
import DrawerPanel from './DrawerPanel.vue'
import { Sheet, SheetContent } from '@/components/ui/sheet'

// 规范 §8.1 的右侧 320 ~ 420px 可收起抽屉：宽屏停靠在页面右侧（380px），窄屏用侧滑面板覆盖页面。
const auth = useAuthStore()
const drawer = useDrawerStore()
const docked = useMediaQuery(DRAWER_DOCKED_QUERY)
// 记住的“打开”只用于宽屏停靠；窄屏上侧滑面板会盖住页面，加载时不自动弹出，只在点“AI 助手”或打开详情时出现
watch(docked, (isDocked) => { if (!isDocked && !drawer.detail) drawer.open = false }, { immediate: true })
function change(open: boolean) {
  if (open) drawer.setOpen(true)
  else drawer.requestClose()
}
function guard(event: Event) {
  if (drawer.detail?.busy) event.preventDefault()
}
</script>

<template>
  <template v-if="auth.user">
    <aside v-if="docked && drawer.open" id="app-drawer" class="app-drawer" aria-label="AI 与详情">
      <DrawerPanel docked />
    </aside>
    <Sheet v-else-if="!docked" :open="drawer.open" @update:open="change">
      <SheetContent id="app-drawer" class="app-drawer-sheet w-full gap-0 p-0 sm:max-w-[400px]" :aria-describedby="undefined" @escape-key-down="guard" @pointer-down-outside="guard" @interact-outside="guard">
        <DrawerPanel :docked="false" />
      </SheetContent>
    </Sheet>
  </template>
</template>
