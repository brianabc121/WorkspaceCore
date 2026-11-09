<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import MailWorkspace from '../features/mail/MailWorkspace.vue'
import '../features/workspace/workspace-pages.css'
// 邮件页（T-0171）：owner / admin；换用户或工作空间时整页重建，邮件内容不跨空间残留
const auth = useAuthStore()
</script>
<template>
  <MailWorkspace v-if="auth.canManage" :key="`${auth.user?.id}:${auth.workspaceId}`" />
  <template v-else>
    <header class="ws-header"><div><h1>邮件</h1><p>查收、阅读、回复已连接的 Gmail 邮箱。</p></div></header>
    <p role="alert">只有 owner / admin 能看邮件。</p>
  </template>
</template>
