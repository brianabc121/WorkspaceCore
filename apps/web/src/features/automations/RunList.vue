<script setup lang="ts">
import { computed } from 'vue'
import { useAuthStore } from '../../stores/auth'
import { useRuns, useAutomationAccess } from './queries'
import { runLabels } from './validation'
import { errorMessage } from '../../services/api'
import { formatTime } from '../time'
const props = defineProps<{ id: string; name: string }>(), auth = useAuthStore(), query = useRuns(auth.workspaceId!, props.id), access = useAutomationAccess()
const items = computed(() => access.denied ? [] : [...new Map(query.data.value?.pages.flatMap(p => p.items).map(r => [r.id, r]) ?? []).values()])
</script>
<template>
  <section :aria-label="name + '运行记录'" role="region">
    <div class="section-heading"><h3>运行记录</h3><button class="secondary-button" :disabled="query.isFetching.value" @click="query.refetch()">刷新运行记录</button></div>
    <p v-if="access.denied" role="alert">无权查看运行记录。</p><p v-else-if="query.isPending.value" role="status">正在加载运行记录…</p>
    <template v-else>
      <p v-if="query.error.value" role="alert">{{ errorMessage(query.error.value) }}</p><p v-if="query.error.value && query.data.value">运行记录可能过期（截至 {{ formatTime(new Date(query.dataUpdatedAt.value).toISOString()) }}）。</p><p v-if="!items.length && !query.error.value">暂无运行记录。</p>
      <div v-if="items.length" class="table-scroll"><table :aria-label="name + '运行记录'"><thead><tr><th>状态</th><th>提交时间</th><th>开始 / 完成</th><th>错误摘要</th></tr></thead><tbody><tr v-for="item in items" :key="item.id"><td>{{ runLabels[item.status] }}</td><td><time :datetime="item.created_at">{{ formatTime(item.created_at) }}</time></td><td>{{ formatTime(item.started_at) }} / {{ formatTime(item.finished_at) }}</td><td class="preserve-text">{{ item.error_summary ?? '—' }}</td></tr></tbody></table></div>
      <button v-if="query.hasNextPage.value" class="secondary-button" :disabled="query.isFetching.value" @click="query.fetchNextPage()">更多运行记录</button>
    </template>
  </section>
</template>
