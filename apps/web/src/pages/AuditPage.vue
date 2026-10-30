<script setup lang="ts">
import { computed } from 'vue'
import { useInfiniteQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../stores/auth'
import { dataOf, isAccessError } from '../services/api'
import DataState from '../components/DataState.vue'
import { formatTime } from '../features/time'
const auth = useAuthStore()
const query = useInfiniteQuery({
  queryKey: computed(() => ['audit', auth.user?.id, auth.workspaceId]),
  enabled: computed(() => !!auth.workspaceId && auth.canManage),
  initialPageParam: null as string | null,
  queryFn: async ({ pageParam, queryKey, signal }) => dataOf(await auth.api.GET('/api/v1/audit-logs', { params: { header: { 'X-Workspace-Id': queryKey[2]! }, query: { limit: 30, ...(pageParam ? { cursor: pageParam } : {}) } }, signal })),
  getNextPageParam: (page) => page.next_cursor ?? undefined,
})
const records = computed(() => isAccessError(query.error.value) ? [] : query.data.value?.pages.flatMap((page) => page.items) ?? [])
</script>
<template>
  <div class="page-heading"><h1>审计日志</h1><p>{{ auth.currentWorkspace?.name }} · 工作空间活动记录，按时间倒序排列。</p></div>
  <section class="panel form-panel">
    <p v-if="!auth.canManage" role="alert">你没有查看审计日志的权限。</p>
    <template v-else>
      <button class="secondary-button" :disabled="query.isFetching.value" @click="query.refetch()">刷新记录</button>
      <DataState :loading="query.isPending.value" :error="query.error.value" :empty="!records.length" empty-text="暂无审计记录" :stale="!!records.length" @retry="query.isFetchNextPageError.value ? query.fetchNextPage() : query.refetch()" />
      <ul class="record-list">
        <li v-for="record in records" :key="record.id">
          <div><strong>{{ record.action }}</strong><p>{{ formatTime(record.created_at) }} · 操作者：{{ record.actor_id ?? '系统 / 未知' }}</p><p>{{ record.resource_type ?? '账户事件' }} {{ record.resource_id }}</p><details><summary>查看详情</summary><pre>{{ JSON.stringify(record.metadata, null, 2) }}</pre></details></div>
        </li>
      </ul>
      <button v-if="query.hasNextPage.value && !isAccessError(query.error.value)" class="secondary-button" :disabled="query.isFetching.value" @click="query.fetchNextPage()">{{ query.isFetchingNextPage.value ? '正在加载…' : '加载更多' }}</button>
    </template>
  </section>
</template>
