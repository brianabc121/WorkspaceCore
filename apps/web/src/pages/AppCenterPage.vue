<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import AppCenterWorkspace from '../features/app-center/AppCenterWorkspace.vue'

// 应用中心（T-0159；T-0211 改版，方案 docs/v2/app-center/）。只有 owner / admin 能打开（路由守卫按侧栏的 minRole 检查）。
// 换身份或工作空间时整页重建：选择、草稿与请求都不会带到新的身份里。
const auth = useAuthStore()
</script>

<template>
  <AppCenterWorkspace v-if="auth.user && auth.workspaceId && auth.canManage" :key="`${auth.user.id}:${auth.workspaceId}`" />
  <p v-else role="alert">只有 owner / admin 能查看应用中心。</p>
</template>
