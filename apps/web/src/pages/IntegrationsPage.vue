<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import IntegrationsWorkspace from '../features/integrations/IntegrationsWorkspace.vue'
const auth = useAuthStore()
</script>
<template><div class="page-heading"><h1>集成设置</h1><p>连接 Vikunja，管理任务服务的认证与同步。</p></div><IntegrationsWorkspace v-if="auth.canManage" :key="`${auth.user?.id}:${auth.workspaceId}`" /><p v-else role="alert">当前角色无权配置集成。</p></template>
