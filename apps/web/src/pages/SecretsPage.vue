<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import ManagementPage from '../components/management/ManagementPage.vue'
import SecretsWorkspace from '../features/agents/SecretsWorkspace.vue'

// 密钥库（T-0196，效果图 07）：只有 owner 能进（路由规则）；换用户或工作空间时整页重建，不串数据
const auth = useAuthStore()
</script>
<template>
  <SecretsWorkspace v-if="auth.workspaceId" :key="auth.user?.id + ':' + auth.workspaceId" />
  <ManagementPage v-else title="密钥库"><p>暂无工作空间，请联系管理员。</p></ManagementPage>
</template>
