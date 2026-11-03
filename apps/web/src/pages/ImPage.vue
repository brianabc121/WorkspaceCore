<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import ImWorkspace from '../features/im/ImWorkspace.vue'
import '../features/im/im.css'

// 消息页（T-0095）：独立页面，不套工作台的侧栏与顶栏；左上角菜单里返回工作台。
const auth = useAuthStore()
</script>

<template>
  <ImWorkspace v-if="auth.workspaceId" :key="auth.user?.id + ':' + auth.workspaceId" />
  <div v-else class="chat-app chat-app-state">
    <div class="chat-state">
      <h1>消息</h1>
      <p>暂无工作空间，请联系管理员。</p>
      <RouterLink to="/" class="chat-state-link">返回工作台</RouterLink>
    </div>
  </div>
</template>
