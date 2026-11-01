<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import AgentsWorkspace from '../features/agents/AgentsWorkspace.vue'
const auth = useAuthStore()
</script>
<template><div class="page-heading"><h1>OpenClaw 实例</h1><p>登记实例、生成接入包、吊销凭证、授权密钥。只有 owner 可以管理。</p></div><AgentsWorkspace v-if="auth.workspaceId" :key="auth.user?.id + ':' + auth.workspaceId" /><p v-else>暂无工作空间，请联系管理员。</p></template>
