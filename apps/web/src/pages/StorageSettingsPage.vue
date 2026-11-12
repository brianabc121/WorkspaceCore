<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import ManagementPage from '../components/management/ManagementPage.vue'
import StorageSettings from '../features/storage/StorageSettings.vue'

// 图片存储（T-0223）：owner 修改，admin 只读（服务端同样校验）。按用户与工作空间重建，换空间不串上一个空间的表单与密钥
const auth = useAuthStore()
</script>

<template>
  <StorageSettings v-if="auth.canManage && auth.workspaceId" :key="`${auth.user?.id}:${auth.workspaceId}`" />
  <ManagementPage v-else title="图片存储"><p role="alert">只有 owner / admin 能查看图片存储设置。</p></ManagementPage>
</template>
