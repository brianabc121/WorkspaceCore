<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import OperationsWorkspace from '../features/operations/OperationsWorkspace.vue'
import '../features/workspace/workspace-pages.css'
// 运维页（T-0177）：页头在 OperationsWorkspace 里（刷新、Grafana 外链）；换用户或工作空间时整页重建
const auth = useAuthStore()
</script>
<template>
  <OperationsWorkspace v-if="auth.workspaceId" :key="auth.user?.id + ':' + auth.workspaceId" />
  <template v-else>
    <header class="ws-header"><div><h1>运维</h1><p>服务健康、关键指标与存储状态。</p></div></header>
    <p>暂无工作空间，请联系管理员。</p>
  </template>
</template>
