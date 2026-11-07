<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import RunnerWorkspace from '../features/runner/RunnerWorkspace.vue'
const auth = useAuthStore()
</script>
<template><div class="page-heading"><h1>运行服务器</h1><p>智能体在这台服务器的容器里执行命令：总览、环境、模板、运行记录与回收策略（owner 与 admin）。实例的“运行环境权限”在 OpenClaw 实例的卡片上打开。</p></div><RunnerWorkspace v-if="auth.canManage" :key="`${auth.user?.id}:${auth.workspaceId}`" /><p v-else role="alert">只有 owner / admin 能管理运行服务器。</p></template>
