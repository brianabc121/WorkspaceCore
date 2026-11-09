<script setup lang="ts">
import { computed } from 'vue'
import { CalendarDays, FileText, Layers, RefreshCw, Waypoints } from '@lucide/vue'
import type { KnowledgeOverviewSummary } from './api'
import { stamp } from './format'

// 知识总览的四个数（T-0173，规范 4.2）：只显示服务端的全量统计。
// 读不到（加载中、失败）就是“—”，不拿图谱节点数、列表长度或缓存顶替；失败时给重试。
const props = defineProps<{ summary: KnowledgeOverviewSummary | undefined; generatedAt?: string; loading: boolean; failed: boolean; scopeLabel: string }>()
const emit = defineEmits<{ retry: [] }>()
const number = new Intl.NumberFormat('zh-CN')
const cards = computed(() => {
  const s = props.summary
  const show = (value: number | undefined) => (value === undefined ? '—' : number.format(value))
  return [
    { key: 'notes', icon: FileText, label: '笔记', value: show(s?.note_count), unit: '篇', hint: '未删除的笔记' },
    { key: 'spaces', icon: Layers, label: '知识库', value: show(s?.space_count), unit: '个', hint: '含已归档、没有笔记的' },
    { key: 'links', icon: Waypoints, label: '笔记关联', value: show(s?.link_count), unit: '条', hint: '笔记之间的 [[链接]]，去重' },
    { key: 'week', icon: CalendarDays, label: '本周更新', value: show(s?.updated_note_count), unit: '篇', hint: '本周一 0 点起改过的笔记' },
  ]
})
</script>

<template>
  <section class="kb-ov-stats-wrap" aria-labelledby="kb-ov-stats-title" :aria-busy="loading">
    <h2 id="kb-ov-stats-title" class="kb-sr">知识库统计：{{ scopeLabel }}</h2>
    <ul class="kb-ov-stats">
      <li v-for="c in cards" :key="c.key" class="kb-ov-stat" :class="`is-${c.key}`" role="group" :aria-label="`${c.label}：${c.value === '—' ? '未知' : c.value + ' ' + c.unit}`" :title="c.hint">
        <span class="kb-ov-stat-icon" aria-hidden="true"><component :is="c.icon" /></span>
        <span class="kb-stack">
          <span class="kb-ov-stat-value kb-num">{{ c.value }}<small v-if="c.value !== '—'">{{ c.unit }}</small></span>
          <span class="kb-ov-stat-label">{{ c.label }}</span>
        </span>
      </li>
    </ul>
    <p v-if="failed" class="kb-ov-note is-error" role="alert">
      统计没有读到，数字显示为“—”。
      <button type="button" class="kb-btn is-small" @click="emit('retry')"><RefreshCw aria-hidden="true" />重试</button>
    </p>
    <p v-else-if="generatedAt" class="kb-ov-note kb-meta">{{ scopeLabel }} · 统计于 {{ stamp(generatedAt) }}</p>
  </section>
</template>
