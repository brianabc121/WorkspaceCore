<script setup lang="ts">
import { computed, watchEffect } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { ArrowRight } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { dataOf, isAccessError, isModuleDisabled } from '../../services/api'
import { safeUrl } from '../../widgets/registry'
import { useWidgetHeader } from '../../widgets/home/header'
import { newsError, newsKey, useNewsToday, type NewsBriefingSummary, type NewsItem } from './api'
import { plainLines } from './format'
import type { BriefingWidgetConfig } from './index'
import './news.css'
import '../../widgets/home/home.css'

// AI 新闻速览（T-0164，首页效果图；原“今日早报”，T-0154）：今天早报的兴趣主题，编号列出，
// 每个主题写命中的前两条，来源链到原文。今天还没有主题数据时，退回显示最新一份早报的开头几行。
// 标题、来源都来自第三方，按纯文本显示；链接只允许 http(s)，新窗口打开
const props = defineProps<{ value: { config: BriefingWidgetConfig } }>()
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id
const header = { 'X-Workspace-Id': scope }
const active = () => user === auth.user?.id && scope === auth.workspaceId
const today = useNewsToday(scope)
const latest = useQuery({
  queryKey: newsKey(user, scope, 'briefings', 'latest'), enabled: computed(active),
  queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/news/briefings', { params: { header, query: { limit: 1 } }, signal })).items[0] ?? null,
})
const MAX_TOPICS = 3
type Source = { name: string; url: string }
type Topic = { name: string; text: string; sources: Source[] }
const topics = computed<Topic[]>(() => (today.data.value?.topics ?? [])
  .filter((topic) => topic.items.length)
  .slice(0, MAX_TOPICS)
  .map((topic) => {
    const first = topic.items.slice(0, 2)
    const text = first.find((item) => item.summary?.trim())?.summary?.trim() ?? first.map((item) => item.title.trim()).join('；')
    const sources: Source[] = []
    for (const item of topic.items as NewsItem[]) {
      const url = item.url
      if (sources.length >= 3 || sources.some((s) => s.name === item.source_name) || !safeUrl(url)) continue
      sources.push({ name: item.source_name, url })
    }
    return { name: topic.name, text, sources }
  }))
const briefing = computed<Pick<NewsBriefingSummary, 'local_date' | 'slot' | 'source' | 'title' | 'excerpt'> | null>(() => {
  const full = today.data.value?.briefing
  if (full) return { local_date: full.local_date, slot: full.slot, source: full.source, title: full.title, excerpt: '' }
  return latest.data.value ?? null
})
const fallback = computed(() => {
  const full = today.data.value?.briefing
  if (full) return plainLines(full.content, props.value.config.lines)
  return latest.data.value?.excerpt ? [latest.data.value.excerpt] : []
})
function when(value: { local_date: string; slot: string }) {
  const now = new Date()
  const local = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
  const day = value.local_date === local ? '今日' : `${Number(value.local_date.slice(5, 7))}月${Number(value.local_date.slice(8, 10))}日`
  return value.slot ? `${day} ${value.slot}` : day
}
// AI 整理：智能体撰写的早报，或 TrendRadar 早报里带了大模型分析
const organized = computed(() => briefing.value?.source === 'agent' || /AI\s*(热点)?分析/.test(today.data.value?.briefing?.content ?? ''))
const widgetHeader = useWidgetHeader()
watchEffect(() => {
  widgetHeader.value = {
    badge: organized.value && topics.value.length ? { text: 'AI 整理', tone: 'accent' } : null,
    meta: briefing.value ? when(briefing.value) : null,
  }
})
const error = computed(() => (today.data.value ? null : today.error.value) ?? latest.error.value)
const pending = computed(() => today.isPending.value && latest.isPending.value)
const number = (index: number) => String(index + 1).padStart(2, '0')
</script>

<template>
  <p v-if="pending" role="status" class="card-state">正在加载早报…</p>
  <p v-else-if="error && isModuleDisabled(error)" class="card-state">功能已停用：新闻早报在当前工作空间没有启用。</p>
  <p v-else-if="error && isAccessError(error)" class="card-state">无权查看早报，请重新登录或联系管理员。</p>
  <div v-else-if="error && !briefing" class="card-state"><p role="alert">{{ newsError(error) }}</p><button type="button" class="secondary-button" @click="today.refetch(); latest.refetch()">重试</button></div>
  <div v-else class="news-digest">
    <ol v-if="topics.length" class="digest-list" aria-label="今日新闻主题">
      <li v-for="(topic, index) in topics" :key="topic.name" class="digest-item">
        <span class="digest-number" aria-hidden="true">{{ number(index) }}</span>
        <div class="digest-body">
          <h3 class="digest-title">{{ topic.name }}</h3>
          <p class="digest-text">{{ topic.text }}</p>
          <p v-if="topic.sources.length" class="digest-sources">来源：<template v-for="(source, i) in topic.sources" :key="source.name"><span v-if="i" aria-hidden="true"> · </span><a :href="source.url" target="_blank" rel="noopener noreferrer">{{ source.name }}</a></template></p>
        </div>
      </li>
    </ol>
    <template v-else-if="briefing">
      <p class="news-widget-title">{{ briefing.title.trim() || '早报' }}</p>
      <ul v-if="fallback.length" class="news-widget-lines" aria-label="早报开头"><li v-for="(line, index) in fallback" :key="index" :title="line">{{ line }}</li></ul>
    </template>
    <p v-else class="card-state">{{ today.data.value?.message || '还没有早报：启用后到了推送时间会自动送达。' }}</p>
    <RouterLink class="home-more" to="/news">{{ briefing ? '阅读完整早报' : '打开早报页' }}<ArrowRight aria-hidden="true" /></RouterLink>
  </div>
</template>
