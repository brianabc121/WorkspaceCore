<script setup lang="ts">
import { computed, ref } from 'vue'
import { useAuthStore } from '../../stores/auth'
import { useNotifications, useNotificationAccess } from './queries'
import NotificationPanel from './NotificationPanel.vue'
const auth = useAuthStore(), open = ref(false), query = useNotifications(auth.workspaceId!, 'count', 1, true), access = useNotificationAccess()
const label = computed(() => access.denied || query.error.value ? '通知，无法获取未读数' : query.data.value ? `通知，${query.data.value.pages[0]!.unread_count} 条未读` : '通知，正在加载未读数')
</script>
<template><button class="theme-button" :aria-label="label" :aria-expanded="open" @click="open = true">{{ label }}</button><NotificationPanel v-if="open" @close="open = false" /></template>
