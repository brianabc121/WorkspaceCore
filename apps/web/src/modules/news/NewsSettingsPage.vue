<script setup lang="ts">
import { useAuthStore } from '../../stores/auth'
import ManagementPage from '../../components/management/ManagementPage.vue'
import NewsSettings from './NewsSettings.vue'

// 设置 → 新闻早报（T-0153；T-0195 改用管理区的页面外壳）：owner / admin 能看（admin 只读），其他角色路由已拦截。
// 换用户或工作空间时整页重建，草稿不会串到另一个工作空间。
const auth = useAuthStore()
</script>

<template>
  <NewsSettings v-if="auth.canManage" :key="`${auth.user?.id}:${auth.workspaceId}`" />
  <ManagementPage v-else title="新闻早报"><p role="alert">只有 owner / admin 能查看新闻早报的设置。</p></ManagementPage>
</template>
