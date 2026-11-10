<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import ManagementPage from '../components/management/ManagementPage.vue'
import ModelConnectionsWorkspace from '../features/model-connections/ModelConnectionsWorkspace.vue'

// 中转站管理（T-0206 / MC-6，效果图 01）：只有 owner 能进（路由规则，服务端同样只给 owner）；
// 换用户或工作空间时整页重建，表单里的 Key、选中的连接与查询都不会串到另一个身份
const auth = useAuthStore()
</script>
<template>
  <ModelConnectionsWorkspace v-if="auth.workspaceId" :key="auth.user?.id + ':' + auth.workspaceId" />
  <ManagementPage v-else title="中转站管理"><p>暂无工作空间，请联系管理员。</p></ManagementPage>
</template>
