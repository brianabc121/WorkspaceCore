<script setup lang="ts">
import { computed } from 'vue'
import { ArrowRight, CalendarDays, CircleAlert, CircleCheck, Clock, FileText, Layers, Tag } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { BRIEFING_STATUS, newsError, type NewsBriefing, type NewsBriefingSummary, type NewsToday } from './api'
import { briefingDay } from './format'

// 早报阅读页右侧栏（T-0174，二批改造规范第 5 节）：只用今天这次响应（主题与已加载的条目）和往期早报列表，
// 不读设置接口（成员、访客也看这一栏）。
// - 你的关注：当前工作空间启用的兴趣主题；owner 有“管理兴趣”，admin 按原规则只读设置，成员与访客没有入口。
// - 本期概况：只数当前已加载的条目与来源（按链接去重），不声称上游全量，也没有“重复报道已合并”这类没有字段支撑的数字。
//   早报状态 complete / receiving / pending / failed 分开写，只有 complete 才说“整理完成”。
// - 往期早报：真实列表的前几份，点开进“往期”里的那一份。
const props = defineProps<{
  today: NewsToday | undefined
  loading: boolean
  archive: NewsBriefingSummary[]
  archiveLoading: boolean
  archiveError: unknown
}>()
const emit = defineEmits<{ retryArchive: [] }>()
const auth = useAuthStore()

const topics = computed(() => props.today?.topics ?? [])
const briefing = computed<NewsBriefing | null>(() => props.today?.briefing ?? null)
const loaded = computed(() => {
  const urls = new Set<string>(), sources = new Set<string>()
  for (const topic of topics.value) {
    for (const item of topic.items) {
      urls.add(item.url)
      sources.add(`${item.source_type}:${item.source_id}`)
    }
  }
  return { items: urls.size, sources: sources.size }
})
// 资讯数据的状态：与早报正文分开（TrendRadar 连不上时早报照样能读）
const dataNote = computed(() => {
  const today = props.today
  if (!today) return ''
  if (today.message) return today.message
  return today.unavailable ? 'TrendRadar 暂时连不上，各主题的条目稍后再看' : ''
})
// TrendRadar 连不上或今天没有主题数据时不显示 0，显示“—”
const countable = computed(() => !!props.today && !props.today.unavailable && topics.value.length > 0)
const parts = computed(() => {
  const value = briefing.value
  if (!value) return ''
  return value.total_parts ? `${value.parts}/${value.total_parts}` : String(value.parts)
})
const state = computed(() => {
  const value = briefing.value
  if (!props.today) return null
  if (!value) return { tone: 'is-muted', text: '今天还没有早报。' }
  switch (value.status) {
    case 'complete': return { tone: 'is-good', text: '这份早报已整理完成。' }
    case 'receiving': return { tone: 'is-warn', text: `正在接收：已收到第 ${parts.value} 批，内容还不完整。` }
    case 'pending': return { tone: 'is-warn', text: '智能体正在写这份早报，还没有写完。' }
    default: return { tone: 'is-bad', text: '这份早报没有生成成功。' }
  }
})
const recent = computed(() => props.archive.filter((item) => item.id !== briefing.value?.id).slice(0, 3))
</script>

<template>
  <aside class="news-side" aria-label="早报侧栏">
    <section class="ws-panel news-side-card" aria-label="你的关注">
      <div class="news-side-title">
        <h2>你的关注</h2>
        <RouterLink v-if="auth.isOwner" class="news-side-link" to="/settings/news">管理兴趣<ArrowRight aria-hidden="true" /></RouterLink>
        <RouterLink v-else-if="auth.canManage" class="news-side-link" to="/settings/news">查看兴趣设置（只读）<ArrowRight aria-hidden="true" /></RouterLink>
      </div>
      <p v-if="loading && !today" class="card-state" role="status">正在加载…</p>
      <ul v-else-if="topics.length" class="news-topic-tags" aria-label="关注的主题">
        <li v-for="topic in topics" :key="topic.name" class="news-topic-tag"><Tag aria-hidden="true" /><span>{{ topic.name }}</span><span class="news-topic-count">{{ topic.items.length }}</span></li>
      </ul>
      <p v-else-if="today && dataNote" class="card-state">暂时读不到关注主题（{{ dataNote }}）。</p>
      <p v-else-if="today" class="card-state">还没有启用的兴趣主题。</p>
      <p v-else class="card-state">—</p>
    </section>

    <section class="ws-panel news-side-card" aria-label="本期概况">
      <div class="news-side-title"><h2>本期概况</h2><span class="news-side-hint">当前已加载</span></div>
      <dl class="news-stats">
        <div class="news-stat"><dt><FileText aria-hidden="true" />相关资讯</dt><dd>{{ countable ? `${loaded.items} 条` : '—' }}</dd></div>
        <div class="news-stat"><dt><Layers aria-hidden="true" />来源</dt><dd>{{ countable ? `${loaded.sources} 个` : '—' }}</dd></div>
      </dl>
      <p class="news-side-hint">只统计今天各兴趣主题里当前已加载的条目（按链接去重），不代表上游的全部资讯。</p>
      <p v-if="state" class="news-state-line" :class="state.tone" role="status">
        <CircleCheck v-if="state.tone === 'is-good'" aria-hidden="true" /><Clock v-else-if="state.tone === 'is-warn'" aria-hidden="true" /><CircleAlert v-else aria-hidden="true" />
        <span>{{ state.text }}</span>
      </p>
      <p v-if="dataNote" class="news-state-line is-muted"><CircleAlert aria-hidden="true" /><span>资讯数据：{{ dataNote }}</span></p>
    </section>

    <section class="ws-panel news-side-card" aria-label="往期早报">
      <div class="news-side-title"><h2>往期早报</h2></div>
      <p v-if="archiveLoading" class="card-state" role="status">正在加载…</p>
      <div v-else-if="archiveError && !archive.length" class="card-state">
        <p role="alert">{{ newsError(archiveError) }}</p>
        <button type="button" class="secondary-button" @click="emit('retryArchive')">重试</button>
      </div>
      <ul v-else-if="recent.length" class="news-recent" aria-label="最近的早报">
        <li v-for="item in recent" :key="item.id">
          <RouterLink class="news-recent-item" :to="{ path: '/news', query: { tab: 'archive', briefing: item.id } }">
            <span class="news-recent-date"><CalendarDays aria-hidden="true" /><time :datetime="item.local_date">{{ briefingDay(item) }}</time></span>
            <span class="news-recent-title">{{ item.title.trim() || '早报' }}</span>
            <span v-if="item.status !== 'complete'" class="badge" :class="`news-status-${item.status}`">{{ BRIEFING_STATUS[item.status] }}</span>
          </RouterLink>
        </li>
      </ul>
      <p v-else class="card-state">还没有往期早报。</p>
      <RouterLink class="news-side-link" :to="{ path: '/news', query: { tab: 'archive' } }">查看全部往期<ArrowRight aria-hidden="true" /></RouterLink>
    </section>
  </aside>
</template>
