<script setup lang="ts">
import DashboardWorkspace from '../features/dashboard/DashboardWorkspace.vue'
import { useAuthStore } from '../stores/auth'
const auth = useAuthStore()
</script>
<template>
  <div class="page-heading"><h1>总览</h1><p>让工作、知识与工具，在一个空间相遇。</p></div>
  <DashboardWorkspace v-if="auth.workspaceId" :key="`${auth.user?.id}:${auth.workspaceId}`" />
  <section v-else class="panel empty-state"><h2>暂无工作空间</h2><p>请联系管理员加入工作空间后使用仪表盘。</p></section>
</template>
