<script setup lang="ts">
import PasswordForm from '../features/PasswordForm.vue'
import SessionList from '../features/SessionList.vue'
</script>
<template>
  <div class="page-heading"><h1>安全设置</h1><p>更新账户密码，检查并撤销自己的登录会话。</p></div>
  <div class="settings-grid"><PasswordForm /><SessionList /></div>
</template>
