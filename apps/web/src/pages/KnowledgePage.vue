<script setup lang="ts">
import { computed, watch } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import { ArrowLeft, BookMarked, Bot, FileText, FolderKanban, History, LayoutDashboard, Library, Moon, Sun, Waypoints } from '@lucide/vue'
import { useAuthStore } from '../stores/auth'
import { useThemeStore } from '../stores/theme'
import '../features/kb/kb.css'

// 知识库（T-0099）：独立页面，不套工作台的侧栏与顶栏。左侧功能条切换总览、笔记、图谱、项目 / 智能体知识库、变更记录、外部文档；
// 手机上功能条换成底部导航（知识库、图谱、笔记、变更）：“知识库”是总览（统计、知识库列表、最近更新，T-0173）。
// 各子页面见 router.ts 的 knowledgeRoutes。
const auth = useAuthStore(), theme = useThemeStore(), route = useRoute()
const section = computed(() => String(route.meta.kbSection ?? 'overview'))
const rail = [
  { id: 'overview', to: '/knowledge', label: '知识总览', icon: LayoutDashboard },
  { id: 'notes', to: '/knowledge/notes', label: '笔记', icon: FileText },
  { id: 'graph', to: '/knowledge/graph', label: '知识图谱', icon: Waypoints },
  { id: 'spaces', to: '/knowledge/spaces', label: '项目知识库', icon: FolderKanban },
  { id: 'agents', to: '/knowledge/agents', label: '智能体知识库', icon: Bot },
  { id: 'changes', to: '/knowledge/changes', label: '变更记录', icon: History },
  { id: 'docs', to: '/knowledge/docs', label: '外部文档（AFFiNE 收藏）', icon: BookMarked },
]
const mobile = [
  { id: 'home', to: '/knowledge', label: '知识库', icon: Library, active: () => route.path === '/knowledge' || ['spaces', 'agents', 'docs'].includes(section.value) },
  { id: 'graph', to: '/knowledge/graph', label: '图谱', icon: Waypoints, active: () => route.path === '/knowledge/graph' },
  { id: 'notes', to: '/knowledge/notes', label: '笔记', icon: FileText, active: () => section.value === 'notes' },
  { id: 'changes', to: '/knowledge/changes', label: '变更', icon: History, active: () => section.value === 'changes' },
]
const isActive = (id: string) => section.value === id
watch(() => route.meta.title, (title) => { document.title = `${String(title ?? '知识库')} · My Workspace` }, { immediate: true })
</script>

<template>
  <div v-if="auth.workspaceId" :key="auth.user?.id + ':' + auth.workspaceId" class="kb-app">
    <nav class="kb-rail" aria-label="知识库功能">
      <RouterLink to="/" class="kb-rail-link" aria-label="返回工作台" title="返回工作台"><ArrowLeft aria-hidden="true" /></RouterLink>
      <span class="kb-rail-sep" aria-hidden="true" />
      <RouterLink v-for="item in rail" :key="item.id" :to="item.to" class="kb-rail-link" :class="{ 'is-active': isActive(item.id) }" :aria-label="item.label" :title="item.label" :aria-current="isActive(item.id) ? 'page' : undefined">
        <component :is="item.icon" aria-hidden="true" />
      </RouterLink>
      <span class="kb-rail-fill" />
      <button type="button" class="kb-rail-link" :aria-label="theme.theme === 'light' ? '切换到暗色主题' : '切换到亮色主题'" :title="theme.theme === 'light' ? '切换到暗色主题' : '切换到亮色主题'" @click="theme.toggle()">
        <Moon v-if="theme.theme === 'light'" aria-hidden="true" /><Sun v-else aria-hidden="true" />
      </button>
    </nav>
    <RouterView />
    <nav class="kb-mobile-nav" aria-label="知识库导航">
      <RouterLink v-for="item in mobile" :key="item.id" :to="item.to" :class="{ 'is-active': item.active() }" :aria-current="item.active() ? 'page' : undefined">
        <component :is="item.icon" aria-hidden="true" /><span>{{ item.label }}</span>
      </RouterLink>
    </nav>
  </div>
  <div v-else class="kb-app">
    <div class="kb-page">
      <h1 class="kb-title">知识库</h1>
      <p>暂无工作空间，请联系管理员。</p>
      <RouterLink to="/">返回工作台</RouterLink>
    </div>
  </div>
</template>
