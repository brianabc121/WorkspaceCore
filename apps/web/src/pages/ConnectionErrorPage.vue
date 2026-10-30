<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { errorMessage } from '../services/api'
import { safeRedirect } from '../app/redirect'
const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
</script>
<template>
  <section class="panel empty-state">
    <h1>暂时无法连接</h1>
    <p role="alert">{{ errorMessage(auth.initializationError) }}</p>
    <button class="primary-link" :disabled="auth.loading" @click="router.replace(safeRedirect(route.query.redirect))">{{ auth.loading ? '正在连接…' : '重新连接' }}</button>
  </section>
</template>
