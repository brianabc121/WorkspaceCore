<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { dataOf, type Schema } from '../../services/api'
import { imError, useContacts, useConversations, useImStatus, type ConversationKind } from './queries'
import ConversationList from './ConversationList.vue'
import ChatWindow from './ChatWindow.vue'

const auth = useAuthStore(), route = useRoute(), router = useRouter(), cache = useQueryClient()
const scope = auth.workspaceId!, user = auth.user?.id
const filter = ref<{ kind: ConversationKind | ''; q: string; group: string }>({ kind: '', q: '', group: '' })
const status = useImStatus(scope), conversations = useConversations(scope, filter), contacts = useContacts(scope)
const items = computed(() => [...new Map((conversations.data.value?.pages ?? []).flatMap(p => p.items).map(c => [c.conversation_id, c])).values()])
const selectedId = computed(() => typeof route.query.c === 'string' ? route.query.c : null)
// The selected conversation may be outside the current filter page; keep the last copy we saw.
const remembered = ref<Schema['ImConversationOut'] | null>(null)
const selected = computed(() => items.value.find(c => c.conversation_id === selectedId.value) ?? (remembered.value?.conversation_id === selectedId.value ? remembered.value : null))
watch(selected, value => { if (value) remembered.value = value })
const names = computed<Record<string, string>>(() => Object.fromEntries((contacts.data.value?.items ?? []).map(c => [c.im_user_id, c.nickname])))
const startable = computed(() => (contacts.data.value?.items ?? []).filter(c => c.instance_id && c.status !== 'disabled'))
const opening = ref(false), openError = ref('')

function select(id: string | null) { void router.replace({ query: { ...route.query, c: id ?? undefined } }) }
async function open(instanceId: string) {
  if (!instanceId || opening.value) return
  opening.value = true; openError.value = ''
  try {
    const conversation = dataOf(await auth.api.POST('/api/v1/im/conversations', { params: { header: { 'X-Workspace-Id': scope } }, body: { instance_id: instanceId } }))
    if (auth.user?.id !== user || auth.workspaceId !== scope) return
    remembered.value = conversation
    await cache.invalidateQueries({ predicate: q => q.queryKey[0] === 'im' && q.queryKey[1] === user && q.queryKey[2] === scope })
    select(conversation.conversation_id)
  } catch (cause) { openError.value = imError(cause) }
  finally { opening.value = false }
}
</script>

<template>
  <p v-if="status.isPending.value" role="status">正在连接 IM…</p>
  <p v-else-if="status.error.value" role="alert">{{ imError(status.error.value) }}</p>
  <p v-else-if="!status.data.value?.available" class="feedback">还没有部署 OpenIM，IM 暂不可用。部署步骤见 docs/deployment.md 第 17 节。</p>
  <p v-else-if="!status.data.value.account_ready" class="feedback">你的 IM 账号正在准备，请稍后刷新。</p>
  <div v-else class="im-layout" :class="{ 'im-layout-chat': !!selected }">
    <div class="im-side">
      <section class="panel im-start" aria-label="开始聊天">
        <label class="form-field"><span>与实例聊天</span>
          <select :disabled="opening || !startable.length" @change="open(($event.target as HTMLSelectElement).value); ($event.target as HTMLSelectElement).value = ''">
            <option value="">{{ startable.length ? '选择实例' : '暂无可聊天的实例' }}</option>
            <option v-for="contact in startable" :key="contact.im_user_id" :value="contact.instance_id!">{{ contact.nickname }}</option>
          </select>
        </label>
        <p v-if="openError" role="alert">{{ openError }}</p>
      </section>
      <ConversationList v-model:filter="filter" :items="items" :selected="selectedId" :loading="conversations.isPending.value" :error="conversations.error.value ? imError(conversations.error.value) : ''" :has-more="!!conversations.hasNextPage.value" :fetching-more="conversations.isFetchingNextPage.value" @select="select" @more="conversations.fetchNextPage()" />
    </div>
    <ChatWindow v-if="selected" :key="selected.conversation_id" :scope="scope" :conversation="selected" :self-id="status.data.value.im_user_id" :names="names" @back="select(null)" />
    <section v-else class="panel im-chat im-chat-empty" aria-label="会话"><p class="empty-state">选择左侧的会话开始聊天。</p></section>
  </div>
</template>
