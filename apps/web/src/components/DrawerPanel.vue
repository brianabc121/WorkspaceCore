<script setup lang="ts">
import { computed, onUnmounted, ref, useId, watch } from 'vue'
import { useAuthStore } from '../stores/auth'
import { useDrawerStore, type DrawerTab } from '../stores/drawer'
import AssistantChat from '../features/im/AssistantChat.vue'
import { Button } from '@/components/ui/button'
import { SheetTitle } from '@/components/ui/sheet'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'

// 抽屉的内容：“AI 助手”与“详情”两页。docked 为 true 时停靠在页面右侧（宽屏），否则放在侧滑面板（Sheet）里。
const props = defineProps<{ docked: boolean }>()
const auth = useAuthStore()
const drawer = useDrawerStore()
const titleId = useId(), detailTitleId = useId()
const title = computed(() => (drawer.tab === 'detail' && drawer.detail ? drawer.detail.title : 'AI 助手'))
const tab = computed({ get: () => drawer.tab, set: (value: string | number) => { drawer.tab = value as DrawerTab } })
const target = ref<HTMLElement | null>(null)
watch(target, (element) => drawer.setTarget(element), { immediate: true })
onUnmounted(() => { if (drawer.target === target.value) drawer.setTarget(null) })
function keys(event: KeyboardEvent) {
  if (!props.docked || event.key !== 'Escape') return
  if (drawer.detail) { event.stopPropagation(); drawer.closeDetail() }
}
</script>

<template>
  <Tabs v-model="tab" class="drawer-panel" @keydown="keys">
    <div class="drawer-header">
      <h2 v-if="docked" :id="titleId">AI 与详情</h2>
      <SheetTitle v-else class="text-base">{{ title }}</SheetTitle>
      <Button v-if="docked" variant="ghost" size="sm" :disabled="drawer.detail?.busy" aria-label="收起 AI 与详情抽屉" @click="drawer.requestClose()">收起</Button>
    </div>
    <TabsList class="drawer-tabs" aria-label="抽屉内容">
      <TabsTrigger value="ai">AI 助手</TabsTrigger>
      <TabsTrigger value="detail" :disabled="!drawer.detail">详情</TabsTrigger>
    </TabsList>
    <TabsContent value="ai" force-mount class="drawer-body" :hidden="drawer.tab !== 'ai'">
      <AssistantChat v-if="auth.user && auth.workspaceId" :key="auth.user.id + ':' + auth.workspaceId" />
    </TabsContent>
    <TabsContent value="detail" force-mount class="drawer-body" :hidden="drawer.tab !== 'detail'">
      <!-- 停靠时详情区自己是非模态对话框；侧滑面板里由 Sheet 承担对话框与标题，这里不再重复 -->
      <section v-if="drawer.detail" class="drawer-detail" :role="docked ? 'dialog' : undefined" :aria-modal="docked ? 'false' : undefined" :aria-labelledby="docked ? detailTitleId : undefined">
        <div v-if="docked" class="section-heading">
          <h3 :id="detailTitleId">{{ drawer.detail.title }}</h3>
          <button type="button" class="icon-button" :disabled="drawer.detail.busy" aria-label="关闭对话框" @click="drawer.closeDetail()">关闭</button>
        </div>
        <div ref="target" />
      </section>
      <p v-else class="empty-state">在任务等页面打开一条记录后，详情会显示在这里。</p>
    </TabsContent>
  </Tabs>
</template>
