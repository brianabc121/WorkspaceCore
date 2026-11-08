<script setup lang="ts">
import { computed } from 'vue'
import { BRIEFING_SOURCE, BRIEFING_STATUS, type NewsBriefing } from './api'
import { briefingHtml, briefingWhen } from './format'
import { formatTime } from '../../features/time'
import '../../features/kb/kb.css'

// 一份早报：标题、日期与推送时间、状态、正文（Markdown 按安全模式渲染，不执行 HTML）
const props = defineProps<{ briefing: NewsBriefing }>()
const html = computed(() => briefingHtml(props.briefing.content))
const title = computed(() => props.briefing.title.trim() || '早报')
const parts = computed(() => (props.briefing.total_parts ? `${props.briefing.parts}/${props.briefing.total_parts}` : String(props.briefing.parts)))
</script>

<template>
  <article class="panel news-card news-briefing" :aria-label="`早报：${title}`">
    <h2>{{ title }}</h2>
    <p class="news-meta">
      <time :datetime="briefing.local_date">{{ briefingWhen(briefing) }}</time>
      <span class="badge" :class="`news-status-${briefing.status}`">{{ BRIEFING_STATUS[briefing.status] }}</span>
      <span>{{ BRIEFING_SOURCE[briefing.source] }}</span>
      <span>更新于 {{ formatTime(briefing.updated_at) }}</span>
      <RouterLink v-if="briefing.task_id" :to="{ path: '/tasks', query: { task: briefing.task_id } }">查看写早报的任务</RouterLink>
    </p>
    <p v-if="briefing.status === 'receiving'" role="status" class="feedback news-banner">正在接收这份早报（已收到第 {{ parts }} 批），内容还不完整。</p>
    <p v-else-if="briefing.status === 'pending'" role="status" class="feedback news-banner">智能体正在写这份早报，写好后这里会更新。</p>
    <p v-else-if="briefing.status === 'failed'" role="alert" class="feedback news-banner">这份早报没有生成成功{{ briefing.error ? `：${briefing.error}` : '。' }}</p>
    <!-- eslint-disable-next-line vue/no-v-html -- briefingHtml 已用 DOMPurify 消毒（features/kb/markdown.ts） -->
    <div v-if="briefing.content.trim()" class="kb-md news-md" v-html="html"></div>
    <p v-else-if="briefing.status === 'complete'" class="card-state">这份早报没有正文。</p>
  </article>
</template>
