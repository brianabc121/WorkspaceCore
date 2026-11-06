<script setup lang="ts">
// Issue 详情页（T-0124）：/issues/:id（通知、搜索的链接），/projects/:project/issues/:number（正文里的 #编号）。
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import DataState from '../components/DataState.vue'
import IssueView from '../features/issues/IssueView.vue'
import { useIssue, useIssueByNumber } from '../features/issues/api'
const auth = useAuthStore(), route = useRoute(), scope = auth.workspaceId ?? ''
const id = computed(() => (route.params.issue ? String(route.params.issue) : null))
const project = computed(() => String(route.params.project ?? ''))
const number = computed(() => (id.value ? 0 : Number(route.params.number ?? 0)))
const byId = useIssue(scope, id), byNumber = useIssueByNumber(scope, project, number)
const query = computed(() => (id.value ? byId : byNumber))
</script>
<template>
  <template v-if="auth.workspaceId">
    <DataState :loading="query.isPending.value" :error="query.error.value" :empty="false" empty-text="" @retry="query.refetch()" />
    <IssueView v-if="query.data.value" :key="query.data.value.id" :issue="query.data.value" />
  </template>
  <p v-else>暂无工作空间。</p>
</template>
