<script setup lang="ts">
import { computed } from 'vue'
import { BRIEFING_SOURCE, BRIEFING_STATUS, type NewsBriefing } from './api'
import { briefingSections, briefingWhen } from './format'
import { formatTime } from '../../features/time'
import '../../features/kb/kb.css'

// 一份早报（T-0174 阅读页）：标题、早报自己的日期与推送时间（briefingWhen，不用浏览器日期）、状态、正文。
// 正文 Markdown 按安全模式渲染（不执行 HTML、不加载外部图片），按真实标题分节：有几节显示几节，
// 至少两节才编号；每节的“来源”只来自这一节正文里的链接。正文段落没有对应的资讯条目，所以这里不放收藏 / 不感兴趣。
const props = defineProps<{ briefing: NewsBriefing }>()
const title = computed(() => props.briefing.title.trim() || '早报')
const parts = computed(() => (props.briefing.total_parts ? `${props.briefing.parts}/${props.briefing.total_parts}` : String(props.briefing.parts)))
const sections = computed(() => {
  const all = briefingSections(props.briefing.content)
  const numbered = all.filter((section) => section.titled).length >= 2
  let count = 0
  return all.map((section) => ({ ...section, number: numbered && section.titled ? String(++count).padStart(2, '0') : '' }))
})
</script>

<template>
  <article class="ws-panel news-reading" :aria-label="`早报：${title}`">
    <header class="news-reading-head">
      <h2>{{ title }}</h2>
      <p class="news-meta">
        <time :datetime="briefing.local_date">{{ briefingWhen(briefing) }}</time>
        <span class="badge" :class="`news-status-${briefing.status}`">{{ BRIEFING_STATUS[briefing.status] }}</span>
        <span class="news-source-badge">{{ BRIEFING_SOURCE[briefing.source] }}</span>
        <span>更新于 {{ formatTime(briefing.updated_at) }}</span>
        <RouterLink v-if="briefing.task_id" :to="{ path: '/tasks', query: { task: briefing.task_id } }">查看写早报的任务</RouterLink>
      </p>
    </header>
    <p v-if="briefing.status === 'receiving'" role="status" class="feedback news-banner">正在接收这份早报（已收到第 {{ parts }} 批），内容还不完整。</p>
    <p v-else-if="briefing.status === 'pending'" role="status" class="feedback news-banner">智能体正在写这份早报，写好后这里会更新。</p>
    <p v-else-if="briefing.status === 'failed'" role="alert" class="feedback news-banner">这份早报没有生成成功{{ briefing.error ? `：${briefing.error}` : '。' }}</p>
    <div v-if="sections.length" class="news-sections">
      <section v-for="(section, index) in sections" :key="index" class="news-section" :class="{ 'is-numbered': section.number }">
        <span v-if="section.number" class="news-section-no" aria-hidden="true">{{ section.number }}</span>
        <div class="news-section-body">
          <!-- eslint-disable-next-line vue/no-v-html -- briefingSections 的片段已用 DOMPurify 消毒（features/kb/markdown.ts） -->
          <div class="kb-md news-md" v-html="section.html"></div>
          <p v-if="section.hosts.length" class="news-section-sources">
            <span class="news-section-label">来源：</span>
            <span v-for="host in section.hosts" :key="host" class="news-source-tag">{{ host }}</span>
            <a v-if="section.link" class="news-read-source" :href="section.link" target="_blank" rel="noopener noreferrer nofollow">阅读来源 →</a>
          </p>
        </div>
      </section>
    </div>
    <p v-else-if="briefing.status === 'complete'" class="card-state">这份早报没有正文。</p>
  </article>
</template>
