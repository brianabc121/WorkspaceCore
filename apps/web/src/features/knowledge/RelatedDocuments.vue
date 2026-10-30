<script setup lang="ts">
import { computed } from 'vue'
import { useInfiniteQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { dataOf, isAccessError } from '../../services/api'
import ExternalLink from '../../components/ExternalLink.vue'
import DataState from '../../components/DataState.vue'
const props = defineProps<{ resourceId: string }>()
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id
const query = useInfiniteQuery({ queryKey: computed(() => ['related-docs', user, scope, props.resourceId]), initialPageParam: null as string | null, queryFn: async ({ pageParam, signal }) => dataOf(await auth.api.GET('/api/v1/knowledge/resources', { params: { header: { 'X-Workspace-Id': scope }, query: { linked_resource_id: props.resourceId, limit: 30, ...(pageParam ? { cursor: pageParam } : {}) } }, signal })), getNextPageParam: (page) => page.next_cursor ?? undefined })
const items = computed(() => isAccessError(query.error.value) ? [] : query.data.value?.pages.flatMap((p) => p.items) ?? [])
</script>
<template><section class="related-documents" aria-label="相关文档"><h3>相关文档</h3><DataState :loading="query.isPending.value" :error="query.error.value" :empty="!items.length" empty-text="暂无相关文档。" :stale="!!items.length" @retry="query.refetch()" /><ul class="quick-links"><li v-for="item in items" :key="item.id">{{ item.title }} <ExternalLink :href="item.url" label="在 AFFiNE 中打开" /></li></ul><button v-if="query.hasNextPage.value && !isAccessError(query.error.value)" type="button" class="secondary-button" :disabled="query.isFetching.value" @click="query.fetchNextPage()">加载更多相关文档</button><RouterLink :to="{ path: '/knowledge', query: { linked_resource_id: resourceId } }">管理相关文档</RouterLink></section></template>
