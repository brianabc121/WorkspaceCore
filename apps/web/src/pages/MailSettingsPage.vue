<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import MailSettings from '../features/mail/MailSettings.vue'
const auth = useAuthStore()
</script>
<template><div class="page-heading"><h1>邮件设置</h1><p>设置 Google OAuth 客户端，连接或断开 Gmail 邮箱，决定智能体能不能访问。</p></div><MailSettings v-if="auth.canManage" :key="`${auth.user?.id}:${auth.workspaceId}`" /><p v-else role="alert">只有 owner / admin 能设置邮件。</p></template>
