<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import ManagementPage from '../components/management/ManagementPage.vue'
import AuditWorkspace from '../features/audit/AuditWorkspace.vue'

// 审计日志（T-0198，效果图 08）：按用户与工作空间重新挂载，切换后不会留下上一位的记录、详情或导出状态
const auth = useAuthStore()
</script>
<template>
  <AuditWorkspace v-if="auth.user && auth.workspaceId && auth.canManage" :key="`${auth.user.id}:${auth.workspaceId}`" />
  <ManagementPage v-else title="审计日志" description="追溯工作空间关键操作，按事件查看相关对象与详情。">
    <p role="alert">你没有查看审计日志的权限。</p>
  </ManagementPage>
</template>
