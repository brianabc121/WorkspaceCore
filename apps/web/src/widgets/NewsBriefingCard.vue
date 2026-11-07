<script setup lang="ts">
import { computed } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../stores/auth'
import { dataOf, isAccessError } from '../services/api'
import { BRIEFING_STATUS, newsError, newsKey } from '../features/news/api'
import { briefingWhen, plainLines } from '../features/news/format'
import type { WidgetValue } from './registry'
import '../features/news/news.css'

// 今日早报（T-0154，NEWS_PLAN 第 7 节）：最新一份早报的时间、标题和正文开头几行（纯文字，去掉 Markdown），点开进早报页。
const props = defineProps<{ value: WidgetValue }>()
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id
const header = { 'X-Workspace-Id': scope }
const lines = computed(() => (props.value.widget_type === 'news_briefing' ? props.value.config.lines : 6))
const active = () => user === auth.user?.id && scope === auth.workspaceId
const latest = useQuery({
  queryKey: newsKey(user, scope, 'briefings', 'latest'), enabled: computed(active),
  queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/news/briefings', { params: { header, query: { limit: 1 } }, signal })).items[0] ?? null,
})
const id = computed(() => latest.data.value?.id ?? null)
const detail = useQuery({
  queryKey: computed(() => newsKey(user, scope, 'briefing', id.value)), enabled: computed(() => active() && !!id.value),
  queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/news/briefings/{briefing_id}', { params: { header, path: { briefing_id: id.value! } }, signal })),
})
const summary = computed(() => latest.data.value ?? null)
const text = computed(() => (detail.data.value ? plainLines(detail.data.value.content, lines.value) : summary.value?.excerpt ? [summary.value.excerpt] : []))
const error = computed(() => latest.error.value ?? (detail.data.value ? null : detail.error.value))
</script>

<template>
  <p v-if="latest.isPending.value" role="status" class="card-state">正在加载早报…</p>
  <p v-else-if="error && isAccessError(error)" class="card-state">无权查看早报，请重新登录或联系管理员。</p>
  <div v-else-if="error" class="card-state"><p role="alert">{{ newsError(error) }}</p><button type="button" class="secondary-button" @click="latest.refetch(); detail.refetch()">重试</button></div>
  <div v-else-if="!summary" class="news-widget">
    <p class="card-state">还没有早报：启用后到了推送时间会自动送达。</p>
    <RouterLink class="primary-link" to="/news">打开早报页 →</RouterLink>
  </div>
  <div v-else class="news-widget">
    <p class="news-meta"><time :datetime="summary.local_date">{{ briefingWhen(summary) }}</time><span v-if="summary.status !== 'complete'" class="badge" :class="`news-status-${summary.status}`">{{ BRIEFING_STATUS[summary.status] }}</span></p>
    <RouterLink class="news-widget-title" to="/news">{{ summary.title.trim() || '早报' }}</RouterLink>
    <p v-if="detail.isPending.value && !text.length" role="status" class="card-state">正在加载正文…</p>
    <ul v-else-if="text.length" class="news-widget-lines" aria-label="早报开头">
      <li v-for="(line, index) in text" :key="index" :title="line">{{ line }}</li>
    </ul>
    <RouterLink class="primary-link" to="/news">查看早报 →</RouterLink>
  </div>
</template>
