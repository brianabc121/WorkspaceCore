<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import IntegrationsWorkspace from '../features/integrations/IntegrationsWorkspace.vue'
const auth = useAuthStore()
</script>
<template><div class="page-heading"><h1>集成设置</h1><p>管理 Vikunja 和 GitHub 的认证、权限检查与同步。</p></div><IntegrationsWorkspace v-if="auth.canManage" :key="`${auth.user?.id}:${auth.workspaceId}`" /><p v-else role="alert">当前角色无权配置集成。</p></template>
