<script setup lang="ts">
import { Info, User } from '@lucide/vue'
import { useAuthStore } from '../stores/auth'
import ManagementPage from '../components/management/ManagementPage.vue'
import PasswordForm from '../features/PasswordForm.vue'
import SessionList from '../features/SessionList.vue'
import '../features/security/security.css'

// 安全设置（T-0193，效果图 04）：当前账户的密码与自己的登录会话，与工作空间无关；不提供 MFA、安全评分或批量退出
const auth = useAuthStore()
</script>
<template>
  <ManagementPage title="安全设置" description="管理账户密码与自己的登录会话。">
    <template v-if="auth.user" #context><span class="mg-tag"><User aria-hidden="true" />{{ auth.user.email }}</span></template>
    <div class="sc-grid">
      <PasswordForm />
      <SessionList />
    </div>
    <section class="ws-panel mg-section sc-note" aria-label="会话管理说明">
      <span class="sc-icon" aria-hidden="true"><Info /></span>
      <div><h2>会话管理说明</h2><p>撤销后该会话立即失效；撤销当前会话会退出登录。修改密码会撤销其他会话，当前会话保持登录。</p></div>
    </section>
  </ManagementPage>
</template>
