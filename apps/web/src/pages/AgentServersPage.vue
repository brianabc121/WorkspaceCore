<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import ServersWorkspace from '../features/agents/ServersWorkspace.vue'
import '../features/workspace/workspace-pages.css'
import '../features/agents/agents-pages.css'
import '../features/agents/agent-servers.css'
// 智能体服务器（T-0181，docs/v2/agent-servers-redesign）：页头在 ServersWorkspace 里；换用户或工作空间时整页重建，
// 上一个空间迟到的响应不会显示在新空间里
const auth = useAuthStore()
</script>
<template>
  <ServersWorkspace v-if="auth.workspaceId" :key="`servers:${auth.user?.id}:${auth.workspaceId}`" />
  <template v-else>
    <header class="ws-header"><div><h1>智能体服务器</h1><p>管理服务器环境，查看其上部署的智能体。</p></div></header>
    <p>暂无工作空间，请联系管理员。</p>
  </template>
</template>
