<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import ManagementPage from '../components/management/ManagementPage.vue'
import IntegrationsWorkspace from '../features/integrations/IntegrationsWorkspace.vue'

// 集成设置（T-0192，效果图 03）：GitHub 由 owner / admin 配置（服务端校验角色）；换用户或换空间时整页重建，不串数据
const auth = useAuthStore()
</script>
<template>
  <IntegrationsWorkspace v-if="auth.canManage" :key="`${auth.user?.id}:${auth.workspaceId}`" />
  <ManagementPage v-else title="集成设置" description="管理 GitHub 认证、仓库权限与同步。"><p role="alert">当前角色无权配置集成。</p></ManagementPage>
</template>
