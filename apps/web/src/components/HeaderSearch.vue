<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { navigation } from '../app/navigation'
import { useAuthStore } from '../stores/auth'
import { useDashboardSearch } from '../stores/dashboardSearch'
import { parseWidget, registry } from '../widgets/registry'
import ModalPanel from './ModalPanel.vue'
const auth = useAuthStore()
const dashboard = useDashboardSearch()
const router = useRouter()
const open = ref(false)
const query = ref('')
function show() { if (auth.user) { open.value = true; query.value = '' } }
function shortcut(event: KeyboardEvent) { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); show() } }
onMounted(() => window.addEventListener('keydown', shortcut))
onUnmounted(() => window.removeEventListener('keydown', shortcut))
const entries = computed(() => [
  ...navigation.filter((item) => item.path !== '/settings/integrations' || auth.canManage).map((item) => ({ label: item.title, detail: '', path: item.path })),
  ...(auth.canManage ? [{ label: '审计日志', detail: '', path: '/settings/audit' }] : []),
  ...dashboard.widgets.flatMap((widget) => {
    const value = parseWidget(widget)
    if (!value) return []
    const detail = value.widget_type === 'note' ? value.config.text.slice(0, 100) : value.widget_type === 'quick_links' ? value.config.links.map((link) => link.title).join(' · ') : ''
    return [{ label: registry[value.widget_type].title, detail, path: `/#widget-${widget.id}` }]
  }),
].filter((entry) => `${entry.label} ${entry.detail}`.toLocaleLowerCase().includes(query.value.trim().toLocaleLowerCase())))
async function go(path: string) {
  open.value = false
  await router.push(path)
  if (path.includes('#widget-')) document.getElementById(path.split('#')[1]!)?.scrollIntoView({ block: 'center' })
}
</script>
<template>
  <button class="theme-button" aria-keyshortcuts="Control+K Meta+K" @click="show">搜索 <kbd>⌘ / Ctrl K</kbd></button>
  <ModalPanel v-if="open && auth.user" title="搜索当前工作空间" @close="open = false">
    <label class="form-field">搜索页面与组件<input v-model="query" type="search" autocomplete="off" autofocus></label><p class="form-help">仅搜索页面导航与已加载的当前首页组件，暂不搜索外部服务。</p>
    <ul class="search-results"><li v-for="entry in entries" :key="entry.path"><button @click="go(entry.path)"><strong>{{ entry.label }}</strong><small v-if="entry.detail">{{ entry.detail }}</small></button></li></ul><p v-if="!entries.length">没有匹配的页面或组件。</p>
  </ModalPanel>
</template>
