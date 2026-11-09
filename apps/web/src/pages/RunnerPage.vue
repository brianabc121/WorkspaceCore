<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import RunnerWorkspace from '../features/runner/RunnerWorkspace.vue'
import '../features/workspace/workspace-pages.css'
import '../features/agents/agents-pages.css'
import '../features/runner/container-server.css'
// 容器服务器（原“运行服务器”；地址仍是 /runner，T-0186）：页头在 RunnerWorkspace 里。
// 换用户或工作空间时整页重建，上一个空间迟到的响应不会显示在新空间里。
const auth = useAuthStore()
</script>
<template>
  <RunnerWorkspace v-if="auth.canManage && auth.workspaceId" :key="`${auth.user?.id}:${auth.workspaceId}`" />
  <template v-else>
    <header class="ws-header"><div><h1>容器服务器</h1><p>统一查看 OpenClaw 申请的容器、资源负载与生命周期。</p></div></header>
    <p role="alert">只有 owner / admin 能管理容器服务器。</p>
  </template>
</template>
