<script setup lang="ts">
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { Waypoints, X } from '@lucide/vue'
import { useOpenTabs } from './tabs'

// 标签栏：知识图谱固定在最前，后面是最近打开的笔记
const { tabs, close } = useOpenTabs()
const route = useRoute(), router = useRouter()
function remove(id: string) {
  const active = route.params.id === id
  close(id)
  if (active) void router.push(tabs.value[0] ? `/knowledge/notes/${tabs.value[0].id}` : '/knowledge')
}
</script>

<template>
  <nav class="kb-tabs" aria-label="打开的页面">
    <RouterLink to="/knowledge" class="kb-tab is-fixed" :class="{ 'is-active': route.meta.kbSection === 'graph' }" :aria-current="route.meta.kbSection === 'graph' ? 'page' : undefined"><Waypoints aria-hidden="true" />知识图谱</RouterLink>
    <span v-for="tab in tabs" :key="tab.id" class="kb-tab" :class="{ 'is-active': route.params.id === tab.id }">
      <RouterLink :to="`/knowledge/notes/${tab.id}`" :aria-current="route.params.id === tab.id ? 'page' : undefined" style="color: inherit; text-decoration: none; max-width: 180px; overflow: hidden; text-overflow: ellipsis">{{ tab.title }}</RouterLink>
      <button type="button" class="kb-tab-close" :aria-label="`关闭 ${tab.title}`" @click="remove(tab.id)"><X aria-hidden="true" /></button>
    </span>
  </nav>
</template>
