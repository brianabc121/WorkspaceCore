<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import ProfileForm from '../features/ProfileForm.vue'
import WorkspaceForm from '../features/WorkspaceForm.vue'
const auth = useAuthStore()
</script>
<template>
  <div class="page-heading"><h1>设置</h1><p>管理个人资料与当前工作空间。</p></div>
  <template v-if="auth.user">
    <div class="settings-grid">
      <ProfileForm />
      <WorkspaceForm v-if="auth.currentWorkspace" :key="auth.workspaceId!" :workspace="auth.currentWorkspace" />
      <section v-else class="panel form-panel"><h2>工作空间</h2><p>你尚未加入任何工作空间，请联系管理员。</p></section>
    </div>
    <RouterLink class="primary-link" to="/settings/security">管理密码与会话 →</RouterLink>
  </template>
  <p v-else role="alert">请先登录以查看设置。</p>
</template>
