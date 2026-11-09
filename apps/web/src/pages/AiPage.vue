<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import ManagementPage from '../components/management/ManagementPage.vue'
import AiWorkspace from '../features/ai/AiWorkspace.vue'

// AI 审批（T-0191，效果图 01）：换用户或工作空间时整页重建，不串上一个身份的数据
const auth = useAuthStore()
</script>
<template>
  <AiWorkspace v-if="auth.workspaceId" :key="auth.user?.id + ':' + auth.workspaceId" />
  <ManagementPage v-else title="AI 审批" description="看清操作影响，逐条审批，跟踪执行结果。"><p>暂无工作空间，请联系管理员。</p></ManagementPage>
</template>
