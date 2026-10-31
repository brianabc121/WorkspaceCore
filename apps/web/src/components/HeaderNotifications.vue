<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import ScopedHeaderNotifications from '../features/notifications/ScopedHeaderNotifications.vue'
const auth = useAuthStore()
</script>
<template><ScopedHeaderNotifications v-if="auth.user && auth.workspaceId" :key="auth.user.id + ':' + auth.workspaceId" /></template>
