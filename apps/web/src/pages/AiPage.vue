<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import AiWorkspace from '../features/ai/AiWorkspace.vue'
const auth = useAuthStore()
</script>
<template><div class="page-heading"><h1>AI 审批</h1><p>审批 OpenClaw 实例发起的写操作，查看每一次工具调用。</p></div><AiWorkspace v-if="auth.workspaceId" :key="auth.user?.id + ':' + auth.workspaceId" /><p v-else>暂无工作空间，请联系管理员。</p></template>
