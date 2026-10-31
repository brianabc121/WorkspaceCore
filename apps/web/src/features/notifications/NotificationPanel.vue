<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useAuthStore } from '../../stores/auth'
import { useNotifications, useNotificationMutation } from './queries'
import NotificationList from './NotificationList.vue'
import ModalPanel from '../../components/ModalPanel.vue'
import { formatTime } from '../time'
import { errorMessage } from '../../services/api'
defineEmits<{ close: [] }>()
const auth = useAuthStore(), scope = auth.workspaceId!, unread = ref(false), query = useNotifications(scope, 'panel', 30, unread)
const mutation = reactive(useNotificationMutation(scope)), denied = computed(() => mutation.access.denied)
const items = computed(() => denied.value ? [] : [...new Map(query.data.value?.pages.flatMap(p => p.items).map(item => [item.id, item]) ?? []).values()])
</script>
<template>
  <ModalPanel title="通知中心" :busy="mutation.access.busy" @close="$emit('close')">
    <div class="form-actions"><label><input v-model="unread" type="checkbox">只看未读</label><button class="secondary-button" :disabled="mutation.access.busy" @click="mutation.refresh">刷新通知</button><button class="primary-button" :disabled="mutation.access.busy || denied || !query.data.value?.pages[0]?.unread_count" @click="mutation.mark({ all: true })">全部标为已读</button></div>
    <p v-if="denied" role="alert">无权查看通知，请重新登录或联系管理员。</p><p v-else-if="query.isPending.value" role="status">正在加载通知…</p>
    <template v-else><p v-if="query.error.value" role="alert">{{ errorMessage(query.error.value) }}</p><p v-if="query.error.value && query.data.value" class="feedback">通知数据可能过期（截至 <time :datetime="new Date(query.dataUpdatedAt.value).toISOString()">{{ formatTime(new Date(query.dataUpdatedAt.value).toISOString()) }}</time>）。</p><p v-if="!items.length && !query.error.value">暂无通知。</p><NotificationList :items="items" :busy="mutation.access.busy" label="通知列表" @read="mutation.mark({ ids: [$event] })" /><button v-if="query.hasNextPage.value" class="secondary-button" :disabled="query.isFetching.value" @click="query.fetchNextPage()">更多通知</button></template>
    <p v-if="mutation.errorText" role="alert">{{ mutation.errorText }}</p><p v-if="mutation.message" role="status">{{ mutation.message }}</p>
  </ModalPanel>
</template>
