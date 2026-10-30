<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import ProjectsWorkspace from '../features/tasks/ProjectsWorkspace.vue'
const auth = useAuthStore()
</script>
<template><div class="page-heading"><h1>项目</h1><p>来自 Vikunja 的项目，任务读写以外部服务为准。</p></div><ProjectsWorkspace v-if="auth.workspaceId" :key="`${auth.user?.id}:${auth.workspaceId}`" /><p v-else>暂无工作空间。</p></template>
