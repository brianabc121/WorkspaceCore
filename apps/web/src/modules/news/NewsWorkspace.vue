<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useIsFetching } from '@tanstack/vue-query'
import { RefreshCw, Settings } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import NewsTodayTab from './NewsTodayTab.vue'
import NewsArchiveTab from './NewsArchiveTab.vue'
import NewsHotTab from './NewsHotTab.vue'
import NewsRssTab from './NewsRssTab.vue'
import NewsSearchTab from './NewsSearchTab.vue'
import NewsFavoritesTab from './NewsFavoritesTab.vue'
import { useNewsActions } from './api'
import '../../features/workspace/workspace-pages.css'
import './news.css'

// 早报页（T-0153，NEWS_PLAN 第 7 节；T-0174 阅读页改版）：六个标签，当前标签写在地址里（?tab=）；
// 通知里的链接 /news?briefing=<id> 直接打开“往期”里的那一份。
// 页头“刷新”只重新读取早报模块的数据（GET），不生成、不推送、不改设置；生成入口仍在设置页，按原权限。
const TABS = [
  { key: 'today', name: '今天' },
  { key: 'archive', name: '往期' },
  { key: 'hot', name: '热榜' },
  { key: 'rss', name: 'RSS' },
  { key: 'search', name: '搜索' },
  { key: 'favorites', name: '收藏' },
] as const
type Tab = typeof TABS[number]['key']
const route = useRoute(), router = useRouter()
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id
const actions = useNewsActions(scope)
const fetching = useIsFetching({ predicate: (query) => query.queryKey[0] === 'news' && query.queryKey[1] === user && query.queryKey[2] === scope })
const briefing = computed(() => (typeof route.query.briefing === 'string' && route.query.briefing ? route.query.briefing : null))
const tab = computed<Tab>(() => {
  if (briefing.value) return 'archive'
  const value = route.query.tab
  return TABS.some(t => t.key === value) ? value as Tab : 'today'
})
function go(next: Tab) { void router.replace({ query: { ...route.query, tab: next === 'today' ? undefined : next, briefing: undefined } }) }
function select(id: string | null) { void router.push({ query: { ...route.query, tab: 'archive', briefing: id ?? undefined } }) }
function refresh() { void actions.refresh() }
</script>

<template>
  <header class="ws-header">
    <div>
      <h1>早报</h1>
      <p>每天的新闻早报、按平台的热榜、RSS 订阅、搜索与收藏。成员可以收藏或标“不感兴趣”，偏好会影响之后的早报。</p>
    </div>
    <div class="ws-actions">
      <button type="button" class="secondary-button" :disabled="fetching > 0" @click="refresh"><RefreshCw aria-hidden="true" />{{ fetching > 0 ? '正在刷新…' : '刷新' }}</button>
      <RouterLink v-if="auth.canManage" class="secondary-button" to="/settings/news"><Settings aria-hidden="true" />早报设置</RouterLink>
    </div>
  </header>
  <div class="news-tabs" role="tablist" aria-label="早报">
    <button v-for="t in TABS" :key="t.key" type="button" role="tab" class="news-tab" :aria-selected="tab === t.key" @click="go(t.key)">{{ t.name }}</button>
  </div>
  <div role="tabpanel" :aria-label="TABS.find(t => t.key === tab)?.name">
    <NewsTodayTab v-if="tab === 'today'" />
    <NewsArchiveTab v-else-if="tab === 'archive'" :selected="briefing" @select="select" />
    <NewsHotTab v-else-if="tab === 'hot'" />
    <NewsRssTab v-else-if="tab === 'rss'" />
    <NewsSearchTab v-else-if="tab === 'search'" />
    <NewsFavoritesTab v-else />
  </div>
</template>
