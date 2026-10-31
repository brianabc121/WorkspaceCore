<script setup lang="ts">
import { computed, reactive } from 'vue'
import { useAuthStore } from '../../stores/auth'
import { useNotifications, useNotificationMutation } from './queries'
import NotificationList from './NotificationList.vue'
import { errorMessage } from '../../services/api'
import { formatTime } from '../time'
const props = defineProps<{ limit: number; unreadOnly: boolean }>(), auth = useAuthStore()
const query = useNotifications(auth.workspaceId!, 'widget', () => props.limit, () => props.unreadOnly), mutation = reactive(useNotificationMutation(auth.workspaceId!))
const items = computed(() => mutation.access.denied ? [] : query.data.value?.pages[0]?.items ?? [])
</script>
<template>
  <p v-if="mutation.access.denied" role="alert">无权查看通知。</p><p v-else-if="query.isPending.value" role="status">正在加载通知…</p>
  <template v-else><p v-if="query.error.value" role="alert">{{ errorMessage(query.error.value) }}</p><p v-if="query.error.value && query.data.value">通知数据可能过期（截至 {{ formatTime(new Date(query.dataUpdatedAt.value).toISOString()) }}）。</p><p v-if="!items.length && !query.error.value">暂无通知。</p><NotificationList :items="items" label="首页通知列表" :busy="mutation.access.busy" @read="mutation.mark({ ids: [$event] })" /></template>
  <p v-if="mutation.errorText" role="alert">{{ mutation.errorText }}</p><button class="secondary-button" :disabled="mutation.access.busy" @click="mutation.refresh">刷新通知组件</button>
</template>
