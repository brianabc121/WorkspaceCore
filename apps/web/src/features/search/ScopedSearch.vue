<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useInfiniteQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { useDashboardSearch } from '../../stores/dashboardSearch'
import { navigation } from '../../app/navigation'
import { dataOf, errorMessage, isAccessError } from '../../services/api'
import { parseWidget, registry } from '../../widgets/registry'
import { formatTime } from '../time'
import { searchTypes, searchTarget, type SearchType } from './searchTargets'
import ModalPanel from '../../components/ModalPanel.vue'
const emit = defineEmits<{ close: [] }>(), auth = useAuthStore(), dashboard = useDashboardSearch(), router = useRouter()
const scope = auth.workspaceId!, user = auth.user?.id, header = { 'X-Workspace-Id': scope }
const input = ref(''), q = ref(''), type = ref<SearchType | ''>(''), results = ref<HTMLElement | null>(null)
let timer: ReturnType<typeof setTimeout> | undefined
watch(input, () => { clearTimeout(timer); timer = setTimeout(() => { q.value = input.value.trim() }, 250) }, { flush: 'sync' })
onBeforeUnmount(() => clearTimeout(timer))
const ready = computed(() => !!q.value && q.value.length <= 100 && input.value.trim() === q.value)
const query = useInfiniteQuery({ queryKey: computed(() => ['search', user, scope, q.value, type.value]), enabled: ready, staleTime: 0, gcTime: 0, initialPageParam: null as string | null, queryFn: async ({ pageParam, signal }) => {
  const revision = dashboard.denialRevision, text = q.value, kind = type.value
  try {
    const data = dataOf(await auth.api.GET('/api/v1/search', { params: { header, query: { q: text, limit: 30, ...(kind ? { types: [kind] } : {}), ...(pageParam ? { cursor: pageParam } : {}) } }, signal }))
    if (auth.user?.id === user && auth.workspaceId === scope && revision === dashboard.denialRevision && text === q.value && kind === type.value && ready.value) dashboard.denied = false
    return data
  } catch (cause) { if (isAccessError(cause) && auth.user?.id === user && auth.workspaceId === scope) dashboard.denyScope(); throw cause }
}, getNextPageParam: (page) => page.next_cursor ?? undefined })
const denied = computed(() => dashboard.denied || isAccessError(query.error.value))
const remote = computed(() => !ready.value || denied.value ? [] : [...new Map((query.data.value?.pages.flatMap((page) => page.items) ?? []).map((item) => [item.resource_type + ':' + item.id, item])).values()])
const groups = computed(() => (Object.keys(searchTypes) as SearchType[]).map((kind) => ({ kind, title: searchTypes[kind], items: remote.value.filter((item) => item.resource_type === kind) })).filter((group) => group.items.length))
const providers = { vikunja: 'Vikunja', github: 'GitHub', affine: 'AFFiNE', local: '工作台日历' }
const entries = computed(() => [
  ...navigation.filter((entry) => entry.path !== '/settings/integrations' || auth.canManage).map((entry) => ({ label: entry.title, detail: '', path: entry.path })),
  ...(auth.canManage ? [{ label: '审计日志', detail: '', path: '/settings/audit' }] : []),
  ...(denied.value ? [] : dashboard.widgets.flatMap((widget) => {
    const value = parseWidget(widget)
    if (!value) return []
    const detail = value.widget_type === 'note' ? value.config.text.slice(0, 100) : value.widget_type === 'quick_links' ? value.config.links.map((link) => link.title).join(' · ') : ''
    return [{ label: registry[value.widget_type].title, detail, path: '/#widget-' + widget.id }]
  })),
].filter((entry) => (entry.label + ' ' + entry.detail).toLocaleLowerCase().includes(input.value.trim().toLocaleLowerCase())))
async function go(path: string) { emit('close'); await router.push(path); if (path.includes('#widget-')) document.getElementById(path.split('#')[1]!)?.scrollIntoView({ block: 'center' }) }
function keys(event: KeyboardEvent) {
  if (event.target instanceof HTMLSelectElement) return
  const controls = [...(results.value?.querySelectorAll<HTMLElement>('[data-search-target]') ?? [])]
  if (!controls.length) return
  const current = controls.indexOf(document.activeElement as HTMLElement)
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    const next = event.key === 'ArrowDown' ? (current + 1) % controls.length : (current < 0 ? controls.length - 1 : (current - 1 + controls.length) % controls.length)
    controls[next]?.focus(); controls[next]?.scrollIntoView?.({ block: 'nearest' })
  } else if (event.key === 'Enter' && (event.target instanceof HTMLInputElement || current >= 0)) { event.preventDefault(); controls[current < 0 ? 0 : current]?.click() }
}
</script>
<template><ModalPanel title="搜索当前工作空间" @close="$emit('close')"><div @keydown="keys"><label class="form-field">搜索页面与组件<input v-model="input" type="search" maxlength="100" autocomplete="off" autofocus aria-describedby="global-search-help"></label><label class="form-field">搜索资源类型<select v-model="type"><option value="">全部资源</option><option v-for="(label, kind) in searchTypes" :key="kind" :value="kind">{{ label }}</option></select></label><p id="global-search-help" class="form-help">搜索任务、项目、收藏、日程和 GitHub 资源；↑ / ↓ 选择，Enter 打开。中文按字面包含匹配，相关性有限。</p><div ref="results"><section v-if="entries.length" aria-label="页面与组件快捷入口"><p class="form-help">页面与组件快捷入口</p><ul class="search-results"><li v-for="entry in entries" :key="entry.path"><button data-search-target @click="go(entry.path)"><strong>{{ entry.label }}</strong><small v-if="entry.detail">{{ entry.detail }}</small></button></li></ul></section><p v-else class="form-help">没有匹配的页面或组件。</p><p v-if="input.trim().length > 100" role="alert">搜索词不能超过 100 字。</p><p v-else-if="!input.trim()" class="form-help">输入关键词搜索当前工作空间的资源。</p><p v-else-if="!ready || query.isPending.value" role="status">正在搜索…</p><template v-else><p v-if="query.error.value || dashboard.denied" role="alert">{{ query.error.value ? errorMessage(query.error.value) : '当前工作空间访问遭拒，请重试验证权限。' }}</p><p v-if="query.error.value && remote.length" class="feedback">当前显示上次搜索的缓存结果，可能已过期。</p><button v-if="query.error.value || dashboard.denied" class="secondary-button" :disabled="query.isFetching.value" @click="query.refetch()">重试搜索</button><p v-if="!query.error.value && !dashboard.denied && !remote.length">没有匹配的资源。</p><section v-for="group in groups" :key="group.kind" :aria-label="'搜索结果：' + group.title"><h3>{{ group.title }}</h3><ul class="search-results"><li v-for="item in group.items" :key="item.id"><a v-if="searchTarget(item).external" data-search-target :href="searchTarget(item).href" target="_blank" rel="noopener noreferrer" @click="$emit('close')"><strong>{{ item.title }}</strong><small>{{ item.context }}</small><small>{{ providers[item.provider] }} · 更新于 {{ formatTime(item.updated_at) }}{{ item.match === 'text' ? ' · 正文匹配' : '' }} · 在原生界面打开 ↗</small></a><RouterLink v-else data-search-target :to="searchTarget(item).href" @click="$emit('close')"><strong>{{ item.title }}</strong><small>{{ item.context }}</small><small>{{ providers[item.provider] }} · 更新于 {{ formatTime(item.updated_at) }}</small></RouterLink></li></ul></section><button v-if="query.hasNextPage.value && !denied" class="secondary-button" :disabled="query.isFetching.value" @click="query.fetchNextPage()">加载更多搜索结果</button></template></div></div></ModalPanel></template>
