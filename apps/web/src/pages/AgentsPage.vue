<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import AgentsWorkspace from '../features/agents/AgentsWorkspace.vue'
import ServersWorkspace from '../features/agents/ServersWorkspace.vue'
// “实例”与“服务器”两个标签（T-0147）：标签记在地址栏（?tab=servers），刷新、分享链接都还在同一页
const auth = useAuthStore(), route = useRoute(), router = useRouter()
const tab = computed(() => route.query.tab === 'servers' ? 'servers' : 'instances')
const go = (next: 'instances' | 'servers') => { if (next !== tab.value) void router.push({ path: '/agents', query: next === 'servers' ? { tab: 'servers' } : {} }) }
</script>
<template>
  <div class="page-heading"><h1>OpenClaw 实例</h1><p>登记实例、生成接入包、部署到服务器、看服务器与每个智能体的负载。只有 owner 可以管理。</p></div>
  <template v-if="auth.workspaceId">
    <div class="segmented agent-tabs" role="group" aria-label="OpenClaw 页面"><button type="button" :aria-pressed="tab === 'instances'" @click="go('instances')">实例</button><button type="button" :aria-pressed="tab === 'servers'" @click="go('servers')">服务器</button></div>
    <ServersWorkspace v-if="tab === 'servers'" :key="`servers:${auth.user?.id}:${auth.workspaceId}`" />
    <AgentsWorkspace v-else :key="auth.user?.id + ':' + auth.workspaceId" />
  </template>
  <p v-else>暂无工作空间，请联系管理员。</p>
</template>
