<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import TasksWorkspace from '../features/tasks/TasksWorkspace.vue'
const auth = useAuthStore()
</script>
<template><div class="page-heading"><h1>任务</h1><p>Vikunja 是任务主源；这里显示已同步的任务投影。</p></div><TasksWorkspace v-if="auth.workspaceId" :key="`${auth.user?.id}:${auth.workspaceId}`" /><p v-else>暂无工作空间，请联系管理员。</p></template>
