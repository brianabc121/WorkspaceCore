<script setup lang="ts">
import { computed, nextTick, ref, toRef, watch } from 'vue'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf, type Schema } from '../../services/api'
import { checkUpload, chronological, imError, useGroup, useMessages, usePins } from './queries'
import MessageItem from './MessageItem.vue'
import GroupDetails from './GroupDetails.vue'
import { useImAction } from './action'

const props = defineProps<{ scope: string; conversation: Schema['ImConversationOut']; selfId: string | null; names: Record<string, string> }>()
defineEmits<{ back: [] }>()
const auth = useAuthStore()
const id = toRef(() => props.conversation.conversation_id)
const messages = useMessages(props.scope, id), pins = usePins(props.scope, id)
const header = { 'X-Workspace-Id': props.scope }
// Messages we sent show up at once; the server list replaces them when the projection catches up.
const pending = ref<Schema['ImMessageOut'][]>([])
const items = computed(() => chronological([...(messages.data.value?.pages ?? []), { items: pending.value, next_cursor: null }]))
watch(() => messages.data.value, data => {
  const known = new Set((data?.pages ?? []).flatMap(p => p.items).map(m => m.server_msg_id ?? m.client_msg_id))
  pending.value = pending.value.filter(m => !known.has(m.server_msg_id ?? m.client_msg_id))
})
const { busy, error, notice, act, active, refresh } = useImAction(props.scope)
const draft = ref(''), list = ref<HTMLElement | null>(null), picker = ref<HTMLInputElement | null>(null), showGroup = ref(false)
// One Idempotency-Key per draft: retrying the same draft after an unknown result can never send it twice.
let draftKey: string | null = null
const canSend = computed(() => props.conversation.kind !== 'notice')
// @ members (groups only; the server rejects mentions in single chats and of non-members). Picking one adds
// "@昵称" to the draft for people and the member's IM ID to at_im_ids for OpenClaw (requireMention).
const MAX_MENTIONS = 20
const group = useGroup(props.scope, toRef(() => props.conversation.kind === 'group' ? props.conversation.group_id : null))
const members = computed(() => (group.data.value?.members ?? []).filter(m => m.im_user_id !== props.selfId))
const mentions = ref<string[]>([]), mentionPick = ref('')
const memberName = (imId: string) => members.value.find(m => m.im_user_id === imId)?.nickname ?? props.names[imId] ?? imId
function mention() {
  const imId = mentionPick.value
  mentionPick.value = ''
  if (!imId || mentions.value.includes(imId)) return
  if (mentions.value.length >= MAX_MENTIONS) { error.value = `一条消息最多 @ ${MAX_MENTIONS} 个成员。`; return }
  mentions.value = [...mentions.value, imId]
  draft.value = `${draft.value}${draft.value && !/\s$/.test(draft.value) ? ' ' : ''}@${memberName(imId)} `
}
const unmention = (imId: string) => { mentions.value = mentions.value.filter(m => m !== imId) }
function sender(message: Schema['ImMessageOut']) {
  return props.names[message.sender_im_id] ?? (props.conversation.kind === 'single' ? props.conversation.title : message.sender_im_id)
}
async function scrollToEnd() { await nextTick(); if (list.value) list.value.scrollTop = list.value.scrollHeight }


async function send(body: Schema['ImMessageSend']) {
  draftKey ??= crypto.randomUUID()
  const key = draftKey
  const ok = await act(async () => {
    try {
      const sent = dataOf(await auth.api.POST('/api/v1/im/conversations/{conversation_id}/messages', { params: { header: { ...header, 'Idempotency-Key': key }, path: { conversation_id: id.value } }, body }))
      if (active() && sent.conversation_id === id.value) pending.value = [...pending.value.filter(m => (m.server_msg_id ?? m.client_msg_id) !== (sent.server_msg_id ?? sent.client_msg_id)), sent]
    } catch (cause) {
      // A definite answer (rejected, not found, validation) ends this draft's key; unknown results keep it for the retry.
      if (cause instanceof ApiError && cause.status >= 400 && cause.status < 500 && cause.status !== 429) draftKey = null
      throw cause
    }
  })
  if (ok) { draftKey = null; void scrollToEnd() }
  return ok
}
async function sendText() {
  const text = draft.value.trim()
  if (!text || !canSend.value) return
  if (await send({ kind: 'text', text, at_im_ids: props.conversation.kind === 'group' ? [...mentions.value] : [] })) { draft.value = ''; mentions.value = [] }
}
async function sendImage(event: Event) {
  const input = event.target as HTMLInputElement, file = input.files?.[0]
  input.value = ''
  if (!file || !canSend.value) return
  if (busy.value) { error.value = '上一条消息还在发送，请稍后再选图片。'; return }
  const problem = checkUpload(file)
  if (problem) { error.value = problem; return }
  let upload: Schema['ImUploadOut'] | null = null
  const uploaded = await act(async () => {
    upload = dataOf(await auth.api.POST('/api/v1/im/uploads', {
      params: { header }, body: file as unknown as string, bodySerializer: (raw: unknown) => raw as File, headers: { 'Content-Type': file.type },
    }))
  })
  if (uploaded && upload) { draftKey = null; await send({ kind: 'image', upload_id: (upload as Schema['ImUploadOut']).upload_id, at_im_ids: [] }) }
}
const setFlag = (patch: Schema['ImConversationUpdate'], success: string) => act(() => auth.api.PATCH('/api/v1/im/conversations/{conversation_id}', { params: { header, path: { conversation_id: id.value } }, body: patch }), success)
const pin = (message: Schema['ImMessageOut']) => act(() => auth.api.POST('/api/v1/im/conversations/{conversation_id}/pins', { params: { header, path: { conversation_id: id.value } }, body: { server_msg_id: message.server_msg_id! } }), '已置顶这条消息')
const unpin = (serverMsgId: string) => act(() => auth.api.DELETE('/api/v1/im/conversations/{conversation_id}/pins/{server_msg_id}', { params: { header, path: { conversation_id: id.value, server_msg_id: serverMsgId } } }), '已取消置顶')

// Mark everything shown as read once per conversation and max_seq, so the badge clears without a loop.
let readSent = ''
watch(() => [id.value, props.conversation.max_seq, props.conversation.unread_count, messages.isSuccess.value] as const, async ([conversation, seq, unread, loaded]) => {
  const mark = `${conversation}:${seq}`
  if (!loaded || !unread || seq <= props.conversation.read_seq || readSent === mark) return
  readSent = mark
  try { await auth.api.POST('/api/v1/im/conversations/{conversation_id}/read', { params: { header, path: { conversation_id: conversation } }, body: { seq } }); if (active()) await refresh() }
  catch { readSent = '' }
}, { immediate: true })
watch(id, () => { draft.value = ''; mentions.value = []; draftKey = null; pending.value = []; error.value = ''; notice.value = ''; void scrollToEnd() })
watch(() => messages.isSuccess.value, ok => { if (ok) void scrollToEnd() }, { immediate: true })
</script>

<template>
  <section class="panel im-chat" :aria-label="`与${conversation.title}的会话`">
    <header class="im-chat-header">
      <button type="button" class="secondary-button im-back" @click="$emit('back')">返回会话列表</button>
      <h2>{{ conversation.title }}</h2>
      <div class="im-chat-actions">
        <button type="button" class="secondary-button" :disabled="busy" :aria-pressed="conversation.pinned" @click="setFlag({ pinned: !conversation.pinned }, conversation.pinned ? '已取消置顶会话' : '已置顶会话')">{{ conversation.pinned ? '取消置顶会话' : '置顶会话' }}</button>
        <button v-if="conversation.kind === 'group' && conversation.group_id" type="button" class="secondary-button" :aria-expanded="showGroup" @click="showGroup = !showGroup">群设置</button>
        <button type="button" class="secondary-button" :disabled="busy" :aria-pressed="conversation.muted" @click="setFlag({ muted: !conversation.muted }, conversation.muted ? '已关闭免打扰' : '已开启免打扰')">{{ conversation.muted ? '关闭免打扰' : '免打扰' }}</button>
      </div>
    </header>
    <GroupDetails v-if="showGroup && conversation.group_id" :scope="scope" :group-id="conversation.group_id" @close="showGroup = false" />
    <section v-if="pins.data.value?.items.length" class="im-pins" aria-label="置顶消息">
      <h3>置顶消息</h3>
      <ul><li v-for="item in pins.data.value.items" :key="item.server_msg_id"><span class="preserve-text">{{ item.message.kind === 'image' ? '[图片]' : item.message.text }}</span><button type="button" class="secondary-button" :disabled="busy" @click="unpin(item.server_msg_id)">取消置顶</button></li></ul>
    </section>
    <div ref="list" class="im-messages" aria-live="polite">
      <button v-if="messages.hasNextPage.value" type="button" class="secondary-button" :disabled="messages.isFetchingNextPage.value" @click="messages.fetchNextPage()">加载更早的消息</button>
      <p v-if="messages.error.value" role="alert">{{ imError(messages.error.value) }}</p>
      <p v-else-if="messages.isPending.value" role="status">正在加载消息…</p>
      <p v-else-if="!items.length" class="empty-state">还没有消息。</p>
      <ol v-else class="im-message-list">
        <MessageItem v-for="message in items" :key="message.server_msg_id ?? message.client_msg_id" :scope="scope" :message="message" :sender="sender(message)" :mine="message.sender_im_id === selfId" :self-id="selfId" :can-pin="message.kind !== 'system'" :locked="busy" @pin="pin(message)" @unpin="unpin(message.server_msg_id!)" />
      </ol>
    </div>
    <p v-if="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
    <form v-if="canSend" class="im-composer" @submit.prevent="sendText">
      <div v-if="conversation.kind === 'group' && members.length" class="im-mentions">
        <label class="form-field"><span>@ 成员</span><select v-model="mentionPick" :disabled="busy" @change="mention"><option value="">选择成员…</option><option v-for="m in members" :key="m.im_user_id" :value="m.im_user_id" :disabled="mentions.includes(m.im_user_id)">{{ m.nickname }}{{ m.instance_id ? '（实例）' : '' }}</option></select></label>
        <ul v-if="mentions.length" aria-label="将 @ 的成员"><li v-for="m in mentions" :key="m"><span>@{{ memberName(m) }}</span><button type="button" class="secondary-button" :aria-label="`不再 @ ${memberName(m)}`" @click="unmention(m)">×</button></li></ul>
      </div>
      <label class="form-field"><span>消息</span><textarea v-model="draft" rows="2" maxlength="4000" placeholder="输入消息，Ctrl + Enter 发送" @keydown.ctrl.enter.prevent="sendText" @keydown.meta.enter.prevent="sendText" /></label>
      <div class="form-actions">
        <input ref="picker" type="file" accept="image/png,image/jpeg,image/gif,image/webp" class="im-file" aria-label="选择图片" @change="sendImage" />
        <button type="button" class="secondary-button" :disabled="busy" @click="picker?.click()">发送图片</button>
        <button type="submit" class="primary-button" :disabled="busy || !draft.trim()">发送</button>
      </div>
    </form>
    <p v-else class="feedback">系统通知会话只能查看。</p>
  </section>
</template>
