<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import ProjectsWorkspace from '../features/tasks/ProjectsWorkspace.vue'
const auth = useAuthStore()
</script>
<!-- 项目页（T-0168）：页头、统计、筛选、卡片与最近动态都在 ProjectsWorkspace 里 -->
<template><ProjectsWorkspace v-if="auth.workspaceId" :key="`${auth.user?.id}:${auth.workspaceId}`" /><template v-else><div class="page-heading"><h1>项目</h1></div><p>暂无工作空间。</p></template></template>
