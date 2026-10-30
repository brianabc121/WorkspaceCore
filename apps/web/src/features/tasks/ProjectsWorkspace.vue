<script setup lang="ts">
import { computed } from 'vue'
import { useAuthStore } from '../../stores/auth'
import { isAccessError } from '../../services/api'
import { useProjects } from './shared'
import ExternalLink from '../../components/ExternalLink.vue'
import SyncNotice from '../../components/SyncNotice.vue'
import DataState from '../../components/DataState.vue'
const query = useProjects(useAuthStore().workspaceId!)
const items = computed(() => isAccessError(query.error.value) ? [] : query.data.value?.pages.flatMap((p) => p.items) ?? [])
</script>
<template><SyncNotice :sync="query.data.value?.pages[0]?.sync" /><DataState :loading="query.isPending.value" :error="query.error.value" :empty="!items.length" empty-text="暂无可用项目，请管理员检查连接或同步状态。" @retry="query.refetch()" /><section v-for="project in items" :key="project.id" class="panel form-panel integration-card"><h2>{{ project.title }}</h2><p class="form-help">Vikunja · {{ project.external_id }}</p><div class="form-actions"><ExternalLink :href="project.source_url" /><RouterLink :to="{ path: '/knowledge', query: { linked_resource_id: project.id } }">相关文档</RouterLink></div></section><button v-if="query.hasNextPage.value" class="secondary-button" :disabled="query.isFetching.value" @click="query.fetchNextPage()">加载更多项目</button></template>
