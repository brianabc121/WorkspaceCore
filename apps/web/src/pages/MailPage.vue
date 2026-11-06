<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import MailWorkspace from '../features/mail/MailWorkspace.vue'
const auth = useAuthStore()
</script>
<template><div class="page-heading"><h1>邮件</h1><p>查收、阅读、回复已连接的 Gmail 邮箱（owner 与 admin）。邮件内容按纯文字显示，不保存在工作台。</p></div><MailWorkspace v-if="auth.canManage" :key="`${auth.user?.id}:${auth.workspaceId}`" /><p v-else role="alert">只有 owner / admin 能看邮件。</p></template>
