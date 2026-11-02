<script setup lang="ts">
import { computed, ref } from 'vue'
import { Bell } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { useNotifications, useNotificationAccess } from './queries'
import NotificationPanel from './NotificationPanel.vue'
const auth = useAuthStore(), open = ref(false), query = useNotifications(auth.workspaceId!, 'count', 1, true), access = useNotificationAccess()
// 顶栏是 sticky 的（会形成层叠上下文）：通知中心挂到 body 下，免得被顶栏的层级盖住（T-0083）
const unread = computed(() => query.data.value?.pages[0]?.unread_count ?? 0)
const label = computed(() => access.denied || query.error.value ? '通知，无法获取未读数' : query.data.value ? `通知，${query.data.value.pages[0]!.unread_count} 条未读` : '通知，正在加载未读数')
</script>
<template><button class="shell-icon" type="button" :aria-label="label" :title="label" :aria-expanded="open" @click="open = true"><Bell aria-hidden="true" /><span v-if="unread && !access.denied && !query.error.value" class="count-badge" aria-hidden="true">{{ unread > 99 ? '99+' : unread }}</span></button><Teleport to="body"><NotificationPanel v-if="open" @close="open = false" /></Teleport></template>
