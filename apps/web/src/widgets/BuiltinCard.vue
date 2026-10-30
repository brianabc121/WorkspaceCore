<script setup lang="ts">
import { computed } from 'vue'
import type { Schema } from '../services/api'
import type { WidgetValue } from './registry'
import { formatTime } from '../features/time'
import ExternalLink from '../components/ExternalLink.vue'
const props = defineProps<{ value: WidgetValue; overview?: Schema['DashboardOverview']; loading?: boolean; failed?: boolean }>()
defineEmits<{ retry: [] }>()
const module = computed(() => props.overview?.modules.tasks)
const state = computed(() => {
  if (props.loading) return 'loading'
  if (props.failed && !props.overview) return 'error'
  const status = module.value?.status ?? 'unavailable'
  return props.failed && status === 'ok' ? 'stale' : status
})
</script>
<template>
  <template v-if="value.widget_type === 'note'">
    <p v-if="value.config.text" class="note-text">{{ value.config.text }}</p><p v-else class="card-state">暂无便签内容</p>
  </template>
  <template v-else-if="value.widget_type === 'quick_links'">
    <ul v-if="value.config.links.length" class="quick-links"><li v-for="(link, index) in value.config.links" :key="index"><a :href="link.url" target="_blank" rel="noopener noreferrer">{{ link.title }} ↗</a></li></ul><p v-else class="card-state">暂无常用链接</p>
  </template>
  <template v-else>
    <p v-if="state === 'loading'" role="status" class="card-state">正在加载组件…</p>
    <p v-else-if="state === 'unavailable'" class="card-state">任务模块暂不可用{{ module?.reason === 'not_configured' ? '，尚未接入任务服务' : '' }}。</p>
    <p v-else-if="state === 'unauthorized'" class="card-state">任务服务授权已失效，请重新连接。</p>
    <p v-else-if="state === 'error'" role="alert" class="card-state">组件暂时无法加载。</p>
    <template v-else>
      <p v-if="state === 'stale'" class="card-state">数据可能已过期 · {{ formatTime(module?.as_of ?? null) }}</p>
      <template v-if="value.widget_type === 'todo_summary'"><p class="summary-number">{{ overview?.summary.pending_tasks ?? '未知' }}</p><p class="form-help">待办任务</p></template>
      <template v-else-if="value.widget_type === 'today_tasks'">
        <ul v-if="overview?.today_tasks.length" class="task-list"><li v-for="task in overview.today_tasks.slice(0, value.config.limit)" :key="task.id">{{ task.title }}<small v-if="task.due_at">{{ formatTime(task.due_at) }}</small><small v-if="task.project_title">{{ task.project_title }}</small><ExternalLink v-if="task.source_url" :href="task.source_url" /></li></ul><p v-else class="card-state">{{ state === 'stale' ? '暂无可用的缓存任务' : '今天暂无任务' }}</p>
      </template>
    </template>
    <button v-if="module?.retryable || state === 'error'" class="secondary-button" :disabled="loading" @click="$emit('retry')">重试组件</button>
  </template>
</template>
