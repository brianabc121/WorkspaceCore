<script setup lang="ts">
import { computed } from 'vue'
import { useInfiniteQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { dataOf, isAccessError, type Schema } from '../../services/api'
import { useProjects } from '../tasks/shared'
import DataState from '../../components/DataState.vue'
const props = defineProps<{ modelValue: string[]; existing: Schema['ResourceLinkOut'][]; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: string[]] }>()
const selection = computed({ get: () => props.modelValue, set: (value: string[]) => emit('update:modelValue', value) })
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id, projects = useProjects(scope)
const tasks = useInfiniteQuery({ queryKey: ['knowledge-link-tasks', user, scope], initialPageParam: null as string | null, queryFn: async ({ pageParam, signal }) => dataOf(await auth.api.GET('/api/v1/tasks', { params: { header: { 'X-Workspace-Id': scope }, query: { status: 'all', limit: 100, ...(pageParam ? { cursor: pageParam } : {}) } }, signal })), getNextPageParam: (page) => page.next_cursor ?? undefined })
const options = computed(() => {
  const all = new Map(props.existing.map((item) => [item.resource_id, `${item.resource_type === 'project' ? '项目' : '任务'} · ${item.title}`]))
  if (!isAccessError(projects.error.value)) for (const item of projects.data.value?.pages.flatMap((p) => p.items) ?? []) all.set(item.id, `项目 · ${item.title}`)
  if (!isAccessError(tasks.error.value)) for (const item of tasks.data.value?.pages.flatMap((p) => p.items) ?? []) all.set(item.id, `任务 · ${item.title}`)
  return [...all].map(([id, title]) => ({ id, title }))
})
</script>
<template><label class="form-field">关联项目与任务<select v-model="selection" multiple size="5" aria-label="关联项目与任务" :disabled="disabled"><option v-for="item in options" :key="item.id" :value="item.id">{{ item.title }}</option></select></label><p class="form-help">最多关联 20 项；使用 Ctrl / Cmd 多选。已关联但尚未加载的项目或任务会保留。</p><DataState :loading="projects.isPending.value || tasks.isPending.value" :error="projects.error.value || tasks.error.value" :empty="false" empty-text="" @retry="projects.refetch(); tasks.refetch()" /><div class="form-actions"><button v-if="projects.hasNextPage.value" type="button" class="secondary-button" :disabled="disabled || projects.isFetching.value" @click="projects.fetchNextPage()">加载更多关联项目</button><button v-if="tasks.hasNextPage.value" type="button" class="secondary-button" :disabled="disabled || tasks.isFetching.value" @click="tasks.fetchNextPage()">加载更多关联任务</button></div></template>
