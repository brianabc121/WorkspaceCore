<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { errorMessage } from '../services/api'
import { safeUrl } from '../widgets/registry'
import { hasRole, pageTarget } from '../app/sidebar'
import { useNavigationLayout } from '../features/navigation/api'
import CustomDashboard from '../features/navigation/CustomDashboard.vue'

// 侧栏里的零代码页面（T-0161）：/p/<id> 显示仪表盘或嵌入页。
// 嵌入页用带沙箱的 iframe，不发 Referer；来源由部署设置 EMBED_ORIGINS 限定（Caddy 的 CSP frame-src 同样只放行这些来源）。
const route = useRoute(), router = useRouter(), auth = useAuthStore()
const layout = useNavigationLayout()
const page = computed(() => {
  const id = String(route.params.id ?? '')
  return layout.data.value?.pages?.find((item) => item.id === id && hasRole(auth.currentWorkspace?.role, item.min_role)) ?? null
})
const url = computed(() => (page.value?.url && safeUrl(page.value.url) ? page.value.url : null))
const origin = computed(() => { try { return url.value ? new URL(url.value).origin : '' } catch { return '' } })
const listed = computed(() => (layout.data.value?.embed_origins ?? []).includes(origin.value))
// 站内页面、笔记从旧书签打开 /p/<id> 时直接转过去
watch(page, (value) => {
  const to = value && (value.kind === 'page' || value.kind === 'note') ? pageTarget(value).to : null
  if (to) void router.replace(to)
}, { immediate: true })
</script>

<template>
  <p v-if="layout.isPending.value" role="status">正在加载…</p>
  <div v-else-if="layout.error.value && !layout.data.value" class="data-state"><p role="alert">{{ errorMessage(layout.error.value) }}</p><button type="button" class="secondary-button" @click="layout.refetch()">重试</button></div>
  <section v-else-if="!page" class="panel empty-state">
    <h1>页面不存在或你没有权限</h1>
    <p>这个页面可能已经从侧栏删除，或者只有更高的角色才能看到。</p>
    <RouterLink class="primary-link" to="/">返回总览</RouterLink>
  </section>
  <div v-else-if="page.kind === 'embed'" class="custom-page">
    <header class="custom-page-header">
      <h1>{{ page.title }}</h1>
      <a v-if="url" :href="url" target="_blank" rel="noopener noreferrer" class="secondary-button">在新窗口打开</a>
    </header>
    <p v-if="url && !listed" class="feedback" role="note">这个来源已不在部署允许嵌入的列表（EMBED_ORIGINS）里，浏览器可能不显示它，可以点“在新窗口打开”。</p>
    <iframe v-if="url" class="custom-embed" :src="url" :title="page.title" sandbox="allow-scripts allow-same-origin allow-forms allow-popups" referrerpolicy="no-referrer"></iframe>
    <p v-else role="alert">这个嵌入页的地址无效。</p>
  </div>
  <div v-else-if="page.kind === 'dashboard' && page.target_id" class="custom-page">
    <header class="custom-page-header"><h1>{{ page.title }}</h1></header>
    <CustomDashboard :dashboard-id="page.target_id" />
  </div>
  <section v-else-if="page.kind === 'link'" class="panel empty-state">
    <h1>{{ page.title }}</h1>
    <p>这是一个外部链接，会在新窗口打开。</p>
    <a v-if="url" class="primary-link" :href="url" target="_blank" rel="noopener noreferrer">打开链接</a>
  </section>
  <p v-else role="status">正在打开…</p>
</template>

<style scoped>
.custom-page { display: grid; gap: 12px; min-width: 0; }
.custom-page-header { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px; min-width: 0; overflow-wrap: anywhere; }
.custom-embed { width: 100%; height: calc(100dvh - var(--topbar-height) - 150px); min-height: 420px; border: 1px solid var(--line); border-radius: 12px; background: var(--surface); }
</style>
