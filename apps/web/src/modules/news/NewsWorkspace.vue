<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import NewsTodayTab from './NewsTodayTab.vue'
import NewsArchiveTab from './NewsArchiveTab.vue'
import NewsHotTab from './NewsHotTab.vue'
import NewsRssTab from './NewsRssTab.vue'
import NewsSearchTab from './NewsSearchTab.vue'
import NewsFavoritesTab from './NewsFavoritesTab.vue'
import './news.css'

// 早报页（T-0153，NEWS_PLAN 第 7 节）：六个标签，当前标签写在地址里（?tab=）；
// 通知里的链接 /news?briefing=<id> 直接打开“往期”里的那一份。
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
const briefing = computed(() => (typeof route.query.briefing === 'string' && route.query.briefing ? route.query.briefing : null))
const tab = computed<Tab>(() => {
  if (briefing.value) return 'archive'
  const value = route.query.tab
  return TABS.some(t => t.key === value) ? value as Tab : 'today'
})
function go(next: Tab) { void router.replace({ query: { ...route.query, tab: next === 'today' ? undefined : next, briefing: undefined } }) }
function select(id: string | null) { void router.push({ query: { ...route.query, tab: 'archive', briefing: id ?? undefined } }) }
</script>

<template>
  <div class="form-actions news-tabs" role="tablist" aria-label="早报">
    <button v-for="t in TABS" :key="t.key" type="button" role="tab" class="secondary-button" :aria-selected="tab === t.key" @click="go(t.key)">{{ t.name }}</button>
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
