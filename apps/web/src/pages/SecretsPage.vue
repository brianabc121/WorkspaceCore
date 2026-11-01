<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import SecretsWorkspace from '../features/agents/SecretsWorkspace.vue'
const auth = useAuthStore()
</script>
<template><div class="page-heading"><h1>密钥库</h1><p>OpenClaw 实例使用的密钥（只写不读）与取用记录。只有 owner 可以管理。</p></div><SecretsWorkspace v-if="auth.workspaceId" :key="auth.user?.id + ':' + auth.workspaceId" /><p v-else>暂无工作空间，请联系管理员。</p></template>
