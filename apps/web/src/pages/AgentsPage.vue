<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import AgentsWorkspace from '../features/agents/AgentsWorkspace.vue'
// OpenClaw 智能体（/agents）。服务器是独立的“智能体服务器”页（/agent-servers，T-0181）；旧的 ?tab=servers 链接由路由转过去
const auth = useAuthStore()
</script>
<template>
  <div class="page-heading"><h1>OpenClaw 智能体</h1><p>登记实例、生成接入包、部署到服务器、看每个智能体的负载。服务器在“智能体服务器”页添加与初始化。只有 owner 可以管理。</p></div>
  <AgentsWorkspace v-if="auth.workspaceId" :key="auth.user?.id + ':' + auth.workspaceId" />
  <p v-else>暂无工作空间，请联系管理员。</p>
</template>
