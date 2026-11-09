<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import AgentsWorkspace from '../features/agents/AgentsWorkspace.vue'
import '../features/workspace/workspace-pages.css'
import '../features/agents/agents-pages.css'
// OpenClaw 智能体（/agents，T-0182）：页头在 AgentsWorkspace 里；换用户或工作空间时整页重建，上一个空间迟到的响应不会显示。
// 服务器是独立的“智能体服务器”页（/agent-servers，T-0181）；旧的 ?tab=servers 链接由路由转过去
const auth = useAuthStore()
</script>
<template>
  <AgentsWorkspace v-if="auth.workspaceId" :key="auth.user?.id + ':' + auth.workspaceId" />
  <template v-else>
    <header class="ws-header"><div><h1>OpenClaw 智能体</h1><p>选择已初始化的服务器，自动部署独立容器。</p></div></header>
    <p>暂无工作空间，请联系管理员。</p>
  </template>
</template>
