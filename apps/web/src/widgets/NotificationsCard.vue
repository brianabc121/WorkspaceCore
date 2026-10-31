<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import ScopedNotificationsCard from '../features/notifications/ScopedNotificationsCard.vue'
import type { WidgetValue } from './registry'
defineOptions({ inheritAttrs: false })
defineProps<{ value: WidgetValue }>()
const auth = useAuthStore()
</script>
<template><ScopedNotificationsCard v-if="auth.user && auth.workspaceId && value.widget_type === 'notifications'" :key="auth.user.id + ':' + auth.workspaceId" :limit="value.config.limit" :unread-only="value.config.unread_only" /></template>
