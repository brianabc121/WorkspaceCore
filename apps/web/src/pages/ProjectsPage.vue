<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import ProjectsWorkspace from '../features/tasks/ProjectsWorkspace.vue'
const auth = useAuthStore()
</script>
<template><div class="page-heading"><h1>项目</h1><p>在工作台里新建和管理项目；删除项目会一起删除它的任务。</p></div><ProjectsWorkspace v-if="auth.workspaceId" :key="`${auth.user?.id}:${auth.workspaceId}`" /><p v-else>暂无工作空间。</p></template>
