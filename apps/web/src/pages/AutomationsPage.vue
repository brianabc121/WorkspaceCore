<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import AutomationsWorkspace from '../features/automations/AutomationsWorkspace.vue'
const auth = useAuthStore()
</script>
<template><div class="page-heading"><h1>自动化</h1><p>白名单工作流与运行记录。</p></div><AutomationsWorkspace v-if="auth.workspaceId" :key="auth.user?.id + ':' + auth.workspaceId" /><p v-else>暂无工作空间，请联系管理员。</p></template>
