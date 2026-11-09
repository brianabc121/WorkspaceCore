<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import DeveloperWorkspace from '../features/developer/DeveloperWorkspace.vue'
import '../features/workspace/workspace-pages.css'
// 开发者页（T-0176）：换用户或工作空间时整页重建，GitHub 数据不跨空间残留
const auth = useAuthStore()
</script>
<template>
  <DeveloperWorkspace v-if="auth.workspaceId" :key="auth.user?.id + ':' + auth.workspaceId" />
  <template v-else>
    <header class="ws-header"><div><h1>开发者</h1><p>GitHub 仓库、Issues、Pull requests 与 Actions 的只读投影。</p></div></header>
    <p>暂无工作空间，请联系管理员。</p>
  </template>
</template>
