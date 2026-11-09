<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import ManagementPage from '../components/management/ManagementPage.vue'
import MailSettings from '../features/mail/MailSettings.vue'

// 邮件设置（T-0194，效果图 05）：owner / admin。按用户与工作空间重建，换空间不串上一个空间的邮箱与草稿
const auth = useAuthStore()
</script>

<template>
  <MailSettings v-if="auth.canManage" :key="`${auth.user?.id}:${auth.workspaceId}`" />
  <ManagementPage v-else title="邮件设置"><p role="alert">只有 owner / admin 能设置邮件。</p></ManagementPage>
</template>
