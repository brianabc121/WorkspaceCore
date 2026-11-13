<script setup lang="ts">
import { useAuthStore } from '../../stores/auth'
import ManagementPage from '../../components/management/ManagementPage.vue'
import LlmBridgeWorkspace from './LlmBridgeWorkspace.vue'

// 大模型客户端管理（docs/v2/LLM_BRIDGE_PLAN.md 第 19 节）：owner / admin 能看（admin 只读），其他角色路由已拦截。
// 换用户或工作空间时整页重建，表单和登录进度不会串到另一个工作空间。
const auth = useAuthStore()
</script>

<template>
  <LlmBridgeWorkspace v-if="auth.canManage && auth.workspaceId" :key="`${auth.user?.id}:${auth.workspaceId}`" :scope="auth.workspaceId" />
  <ManagementPage v-else title="大模型客户端管理"><p role="alert">只有 owner / admin 能查看大模型客户端管理。</p></ManagementPage>
</template>
