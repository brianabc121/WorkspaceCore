<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import TasksWorkspace from '../features/tasks/TasksWorkspace.vue'
const auth = useAuthStore()
</script>
<!-- 任务页（T-0167）：页头、状态筛选、列表与详情都在 TasksWorkspace 里 -->
<template><TasksWorkspace v-if="auth.workspaceId" :key="`${auth.user?.id}:${auth.workspaceId}`" /><template v-else><div class="page-heading"><h1>任务</h1></div><p>暂无工作空间，请联系管理员。</p></template></template>
