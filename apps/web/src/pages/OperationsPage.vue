<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import OperationsWorkspace from '../features/operations/OperationsWorkspace.vue'
const auth = useAuthStore()
</script>
<template><div class="page-heading"><h1>运维</h1><p>服务健康、检查结果与预设指标。</p></div><OperationsWorkspace v-if="auth.workspaceId" :key="auth.user?.id + ':' + auth.workspaceId" /><p v-else>暂无工作空间，请联系管理员。</p></template>
