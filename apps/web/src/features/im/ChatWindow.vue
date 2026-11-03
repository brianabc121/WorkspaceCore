<script setup lang="ts">
import { computed, nextTick, ref, toRef, watch } from 'vue'
import { ArrowLeft, AtSign, Bell, BellOff, EllipsisVertical, Download, PanelRight, Paperclip, Pin, PinOff, Send, X } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf, type Schema } from '../../services/api'
import { STATUS_LABELS, checkUpload, chronological, imError, useGroup, useMessages, usePins } from './queries'
import { useConversationActions } from './action'
import { dayLabel, sameDay, within } from './format'
import { usePopover } from './usePopover'
import { useMediaQuery } from '../../composables/useMediaQuery'
import MessageItem from './MessageItem.vue'
import ChatAvatar from './ChatAvatar.vue'

// 消息页中间的聊天区（T-0095）：顶栏、置顶消息条、按日期分隔的消息气泡、输入区。
const props = defineProps<{ scope: string; conversation: Schema['ImConversationOut']; selfId: string | null; names: Record<string, string>; peerStatus: Schema['ImContactOut']['status'] | null; infoOpen: boolean }>()
defineEmits<{ back: []; info: [] }>()
const auth = useAuthStore()
const id = toRef(() => props.conversation.conversation_id)
const messages = useMessages(props.scope, id), pins = usePins(props.scope, id)
const header = { 'X-Workspace-Id': props.scope }
const wide = useMediaQuery('(min-width: 768px)')
// Messages we sent show up at once; the server list replaces them when the projection catches up.
// While a send is in flight the server already lists it as a "sending" row whose client_msg_id is
// `myws-<Idempotency-Key>` (no server_msg_id yet). Once we hold the sent copy that row is the same message, so it is
// hidden instead of showing the message twice until the next refresh. Either ID matching means the server caught up.
type Sent = { key: string; message: Schema['ImMessageOut'] }
const pending = ref<Sent[]>([])
const idsOf = (m: Schema['ImMessageOut']) => [m.server_msg_id, m.client_msg_id].filter((v): v is string => !!v)
const listedIds = (pages: Schema['ImMessagePage'][]) => new Set(pages.flatMap(p => p.items).flatMap(idsOf))
const items = computed(() => {
  const pages = messages.data.value?.pages ?? [], listed = listedIds(pages)
  const placeholders = new Set(pending.value.map(s => `myws-${s.key}`))
  const server = pages.map(p => ({ ...p, items: p.items.filter(m => !placeholders.has(m.client_msg_id)) }))
  const unsent = pending.value.map(s => s.message).filter(m => !idsOf(m).some(v => listed.has(v)))
  return chronological([...server, { items: unsent, next_cursor: null }])
})
watch(() => messages.data.value, data => {
  const listed = listedIds(data?.pages ?? [])
  pending.value = pending.value.filter(s => !idsOf(s.message).some(v => listed.has(v)))
})
const { busy, error, notice, act, active, refresh, setFlag, exportConversation } = useConversationActions(props.scope, () => id.value)
const draft = ref(''), list = ref<HTMLElement | null>(null), picker = ref<HTMLInputElement | null>(null), box = ref<HTMLTextAreaElement | null>(null)
// One Idempotency-Key per message being sent: retrying it after an unknown result can never send it twice.
// The draft, each re-sent failed message and each image have their own key, so sending one never drops another's
// key while its result is still unknown.
type Slot = { key: string | null }
const draftSlot: Slot = { key: null }
const resendSlots = new Map<string, Slot>()

// 群聊：成员（@、顶栏的人数与在线实例数）与是否已解散
const group = useGroup(props.scope, toRef(() => props.conversation.kind === 'group' ? props.conversation.group_id : null))
const allMembers = computed(() => group.data.value?.members ?? [])
const members = computed(() => allMembers.value.filter(m => m.im_user_id !== props.selfId))
const dismissed = computed(() => !!group.data.value?.dismissed)
const canSend = computed(() => props.conversation.kind !== 'notice' && !dismissed.value)
const subtitle = computed(() => {
  if (props.conversation.kind === 'notice') return '系统通知'
  if (props.conversation.kind === 'single') return props.peerStatus ? `实例 · ${STATUS_LABELS[props.peerStatus]}` : '实例'
  if (!group.data.value) return '群聊'
  const online = allMembers.value.filter(m => m.instance_id && m.online).length
  return `${allMembers.value.length} 名成员${online ? `，${online} 个实例在线` : ''}${dismissed.value ? '（已解散）' : ''}`
})

// @ members (groups only; the server rejects mentions in single chats and of non-members). Picking one adds
// "@昵称" to the draft for people and the member's IM ID to at_im_ids for OpenClaw (requireMention).
const MAX_MENTIONS = 20
const mentions = ref<string[]>([]), mentionMenu = usePopover()
const memberName = (imId: string) => members.value.find(m => m.im_user_id === imId)?.nickname ?? props.names[imId] ?? imId
function mention(imId: string) {
  mentionMenu.close()
  if (mentions.value.includes(imId)) return
  if (mentions.value.length >= MAX_MENTIONS) { error.value = `一条消息最多 @ ${MAX_MENTIONS} 个成员。`; return }
  mentions.value = [...mentions.value, imId]
  draft.value = `${draft.value}${draft.value && !/\s$/.test(draft.value) ? ' ' : ''}@${memberName(imId)} `
  void nextTick(() => box.value?.focus())
}
const unmention = (imId: string) => { mentions.value = mentions.value.filter(m => m !== imId) }
// 工作台的通知账号（ws_notice：审批卡片、“自动接力已暂停”等提示）
const NOTICE_ACCOUNT = 'ws_notice'
function sender(message: Schema['ImMessageOut']) {
  if (message.sender_im_id === NOTICE_ACCOUNT) return '工作台通知'
  return props.names[message.sender_im_id] ?? allMembers.value.find(m => m.im_user_id === message.sender_im_id)?.nickname ?? (props.conversation.kind === 'single' ? props.conversation.title : message.sender_im_id)
}

// 日期分隔；同一个人五分钟内连续发的消息合成一组（只在第一条显示名字、最后一条旁边显示头像）
type Row = { type: 'day'; key: string; label: string } | { type: 'message'; key: string; message: Schema['ImMessageOut']; first: boolean; last: boolean }
const joins = (a: Schema['ImMessageOut'] | undefined, b: Schema['ImMessageOut'] | undefined) =>
  !!a && !!b && a.kind !== 'system' && b.kind !== 'system' && a.sender_im_id === b.sender_im_id && sameDay(a.sent_at, b.sent_at) && within(a.sent_at, b.sent_at, 5)
const rows = computed<Row[]>(() => {
  const all = items.value, out: Row[] = []
  all.forEach((message, index) => {
    const before = all[index - 1], after = all[index + 1], key = message.server_msg_id ?? message.client_msg_id
    if (!before || !sameDay(before.sent_at, message.sent_at)) out.push({ type: 'day', key: `day:${key}`, label: dayLabel(message.sent_at) })
    out.push({ type: 'message', key, message, first: !joins(before, message), last: !joins(message, after) })
  })
  return out
})
async function scrollToEnd() { await nextTick(); if (list.value) list.value.scrollTop = list.value.scrollHeight }

// 输入框随内容变高（最多约 6 行）
function grow() { const el = box.value; if (!el) return; el.style.height = 'auto'; el.style.height = `${Math.min(el.scrollHeight, 160)}px` }
watch(draft, () => void nextTick(grow))
// 电脑上 Enter 发送、Shift + Enter 换行；手机上 Enter 换行、点按钮发送；Ctrl / ⌘ + Enter 都能发送。输入法选字时的 Enter 不发送
function keydown(event: KeyboardEvent) {
  if (event.key !== 'Enter' || event.isComposing) return
  if (event.ctrlKey || event.metaKey || (wide.value && !event.shiftKey && !event.altKey)) { event.preventDefault(); void sendText() }
}

async function send(body: Schema['ImMessageSend'], slot: Slot = draftSlot) {
  slot.key ??= crypto.randomUUID()
  const key = slot.key
  const ok = await act(async () => {
    try {
      const sent = dataOf(await auth.api.POST('/api/v1/im/conversations/{conversation_id}/messages', { params: { header: { ...header, 'Idempotency-Key': key }, path: { conversation_id: id.value } }, body }))
      if (active() && sent.conversation_id === id.value) pending.value = [...pending.value.filter(s => s.key !== key), { key, message: sent }]
    } catch (cause) {
      // A definite answer (rejected, not found, validation) ends this draft's key; unknown results keep it for the retry.
      if (cause instanceof ApiError && cause.status >= 400 && cause.status < 500 && cause.status !== 429) slot.key = null
      throw cause
    }
  })
  if (ok) { slot.key = null; void scrollToEnd() }
  return ok
}
async function sendText() {
  const text = draft.value.trim()
  if (!text || !canSend.value) return
  if (await send({ kind: 'text', text, at_im_ids: props.conversation.kind === 'group' ? [...mentions.value] : [] })) { draft.value = ''; mentions.value = [] }
}
// 确定没有发出的文字消息（发送失败）可以重新发送：作为一条新消息，用这条消息自己的幂等键（结果未知时再点仍用同一个）
async function resend(message: Schema['ImMessageOut']) {
  if (!message.text || busy.value || !canSend.value) return
  const id = message.client_msg_id
  const slot = resendSlots.get(id) ?? { key: null }
  resendSlots.set(id, slot)
  if (await send({ kind: 'text', text: message.text, at_im_ids: props.conversation.kind === 'group' ? [...message.at_im_ids] : [] }, slot)) resendSlots.delete(id)
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
  if (uploaded && upload) await send({ kind: 'image', upload_id: (upload as Schema['ImUploadOut']).upload_id, at_im_ids: [] }, { key: null })
}
const pin = (message: Schema['ImMessageOut']) => act(() => auth.api.POST('/api/v1/im/conversations/{conversation_id}/pins', { params: { header, path: { conversation_id: id.value } }, body: { server_msg_id: message.server_msg_id! } }), '已置顶这条消息')
const unpin = (serverMsgId: string) => act(() => auth.api.DELETE('/api/v1/im/conversations/{conversation_id}/pins/{server_msg_id}', { params: { header, path: { conversation_id: id.value, server_msg_id: serverMsgId } } }), '已取消置顶')

// 置顶消息条：一次显示一条，点一下跳到那条消息并换到下一条
const pinned = computed(() => pins.data.value?.items ?? [])
const pinIndex = ref(0)
const currentPin = computed(() => pinned.value.length ? pinned.value[pinIndex.value % pinned.value.length] : null)
const pinText = (item: { message: Schema['ImMessageOut'] }) => item.message.kind === 'image' ? '[图片]' : item.message.kind === 'approval_card' ? '[审批卡片]' : (item.message.text ?? '')
// 置顶的消息不在已加载的消息里时，先往前加载（最多 10 页），仍找不到就把全文显示出来
const MAX_PIN_PAGES = 10
const findMessage = (serverMsgId: string) => list.value?.querySelector<HTMLElement>(`[data-msg="${CSS.escape(serverMsgId)}"]`) ?? null
async function jumpToPin() {
  const item = currentPin.value
  if (!item) return
  pinIndex.value = (pinIndex.value + 1) % pinned.value.length
  let el = findMessage(item.server_msg_id)
  for (let page = 0; !el && page < MAX_PIN_PAGES && messages.hasNextPage.value; page++) {
    await messages.fetchNextPage()
    await nextTick()
    el = findMessage(item.server_msg_id)
  }
  if (!el) { notice.value = `置顶消息：${pinText(item)}`; return }
  el.scrollIntoView?.({ block: 'center', behavior: 'smooth' })
  el.classList.add('is-flash'); setTimeout(() => el?.classList.remove('is-flash'), 1200)
}

const more = usePopover()
function exportFromMenu() { more.close(); void exportConversation() }

// Mark everything shown as read once per conversation and max_seq, so the badge clears without a loop.
let readSent = ''
watch(() => [id.value, props.conversation.max_seq, props.conversation.unread_count, messages.isSuccess.value] as const, async ([conversation, seq, unread, loaded]) => {
  const mark = `${conversation}:${seq}`
  if (!loaded || !unread || seq <= props.conversation.read_seq || readSent === mark) return
  readSent = mark
  try { await auth.api.POST('/api/v1/im/conversations/{conversation_id}/read', { params: { header, path: { conversation_id: conversation } }, body: { seq } }); if (active()) await refresh() }
  catch { readSent = '' }
}, { immediate: true })
watch(id, () => { draft.value = ''; mentions.value = []; draftSlot.key = null; resendSlots.clear(); pending.value = []; error.value = ''; notice.value = ''; pinIndex.value = 0; void scrollToEnd() })
watch(() => messages.isSuccess.value, ok => { if (ok) void scrollToEnd() }, { immediate: true })
const infoLabel = computed(() => props.conversation.kind === 'group' ? '群信息' : '会话信息')
</script>

<template>
  <section :aria-label="`与${conversation.title}的会话`">
    <header class="chat-bar chat-head">
      <button type="button" class="chat-icon-button chat-back" aria-label="返回会话列表" @click="$emit('back')"><ArrowLeft aria-hidden="true" /></button>
      <div class="chat-head-who">
        <ChatAvatar :name="conversation.title" size="md" :notice="conversation.kind === 'notice'" :status="peerStatus" />
        <div class="chat-head-text">
          <h2><button type="button" class="chat-head-title" :title="`打开${infoLabel}`" @click="$emit('info')">{{ conversation.title }}</button></h2>
          <span class="chat-head-sub">{{ subtitle }}</span>
        </div>
      </div>
      <div class="chat-head-actions">
        <button type="button" class="chat-icon-button chat-head-optional" :class="{ 'is-on': conversation.pinned }" :disabled="busy" aria-label="置顶会话" :aria-pressed="conversation.pinned" @click="setFlag({ pinned: !conversation.pinned }, conversation.pinned ? '已取消置顶会话' : '已置顶会话')"><Pin aria-hidden="true" /></button>
        <button type="button" class="chat-icon-button chat-head-optional" :class="{ 'is-on': conversation.muted }" :disabled="busy" aria-label="免打扰" :aria-pressed="conversation.muted" @click="setFlag({ muted: !conversation.muted }, conversation.muted ? '已关闭免打扰' : '已开启免打扰')"><BellOff v-if="conversation.muted" aria-hidden="true" /><Bell v-else aria-hidden="true" /></button>
        <button type="button" class="chat-icon-button chat-head-optional" :class="{ 'is-on': infoOpen }" :aria-label="infoLabel" :aria-expanded="infoOpen" @click="$emit('info')"><PanelRight aria-hidden="true" /></button>
        <div :ref="(el) => { more.root.value = el as HTMLElement | null }" class="chat-pop">
          <button type="button" class="chat-icon-button" aria-label="更多" aria-haspopup="menu" :aria-expanded="more.open.value" @click="more.toggle"><EllipsisVertical aria-hidden="true" /></button>
          <div v-if="more.open.value" class="chat-menu chat-menu-right" role="menu" aria-label="更多">
            <button type="button" role="menuitem" class="chat-menu-item chat-menu-narrow" :disabled="busy" @click="more.close(); setFlag({ pinned: !conversation.pinned }, conversation.pinned ? '已取消置顶会话' : '已置顶会话')"><Pin aria-hidden="true" />{{ conversation.pinned ? '取消置顶会话' : '置顶会话' }}</button>
            <button type="button" role="menuitem" class="chat-menu-item chat-menu-narrow" :disabled="busy" @click="more.close(); setFlag({ muted: !conversation.muted }, conversation.muted ? '已关闭免打扰' : '已开启免打扰')"><BellOff aria-hidden="true" />{{ conversation.muted ? '关闭免打扰' : '免打扰' }}</button>
            <button type="button" role="menuitem" class="chat-menu-item" :disabled="busy" @click="exportFromMenu"><Download aria-hidden="true" />导出聊天记录</button>
          </div>
        </div>
      </div>
    </header>

    <div v-if="currentPin" class="chat-pinbar">
      <button type="button" class="chat-pinbar-main" :aria-label="`置顶消息：${pinText(currentPin)}`" @click="jumpToPin">
        <span class="chat-pinbar-mark" aria-hidden="true"></span>
        <span class="chat-pinbar-text"><span class="chat-pinbar-label">置顶消息<template v-if="pinned.length > 1">（{{ pinIndex % pinned.length + 1 }}/{{ pinned.length }}）</template></span><span class="chat-pinbar-body" :title="pinText(currentPin)">{{ pinText(currentPin) }}</span></span>
      </button>
      <button type="button" class="chat-icon-button" :disabled="busy" aria-label="取消置顶这条消息" @click="unpin(currentPin.server_msg_id)"><PinOff aria-hidden="true" /></button>
    </div>

    <div ref="list" class="chat-scroll" aria-live="polite">
      <div class="chat-column">
        <button v-if="messages.hasNextPage.value" type="button" class="chat-chip chat-chip-button" :disabled="messages.isFetchingNextPage.value" @click="messages.fetchNextPage()">{{ messages.isFetchingNextPage.value ? '正在加载…' : '加载更早的消息' }}</button>
        <p v-if="messages.error.value" role="alert" class="chat-chip">{{ imError(messages.error.value) }}</p>
        <p v-else-if="messages.isPending.value" role="status" class="chat-chip">正在加载消息…</p>
        <p v-else-if="!items.length" class="chat-chip">还没有消息。</p>
        <ol v-else class="chat-messages">
          <template v-for="row in rows" :key="row.key">
            <li v-if="row.type === 'day'" class="chat-day"><span class="chat-chip">{{ row.label }}</span></li>
            <MessageItem
              v-else :scope="scope" :message="row.message" :sender="sender(row.message)" :mine="row.message.sender_im_id === selfId" :self-id="selfId" :group="conversation.kind === 'group'" :first="row.first" :last="row.last"
              :can-pin="row.message.kind !== 'system'" :can-resend="canSend" :locked="busy" @pin="pin(row.message)" @unpin="unpin(row.message.server_msg_id!)" @resend="resend(row.message)"
            />
          </template>
        </ol>
      </div>
    </div>

    <div class="chat-column chat-foot">
      <p v-if="error" role="alert" class="chat-toast is-error">{{ error }}</p>
      <p v-if="notice" role="status" class="chat-toast">{{ notice }}</p>
      <form v-if="canSend" class="chat-composer" @submit.prevent="sendText">
        <ul v-if="mentions.length" class="chat-mention-chips" aria-label="将 @ 的成员">
          <li v-for="m in mentions" :key="m"><span>@{{ memberName(m) }}</span><button type="button" :aria-label="`不再 @ ${memberName(m)}`" @click="unmention(m)"><X aria-hidden="true" /></button></li>
        </ul>
        <div class="chat-composer-row">
          <div class="chat-input">
            <div v-if="conversation.kind === 'group' && members.length" :ref="(el) => { mentionMenu.root.value = el as HTMLElement | null }" class="chat-pop">
              <button type="button" class="chat-icon-button" :disabled="busy" aria-label="@ 成员" aria-haspopup="menu" :aria-expanded="mentionMenu.open.value" @click="mentionMenu.toggle"><AtSign aria-hidden="true" /></button>
              <div v-if="mentionMenu.open.value" class="chat-menu chat-menu-up" role="menu" aria-label="选择要 @ 的成员">
                <button v-for="m in members" :key="m.im_user_id" type="button" role="menuitem" class="chat-menu-item" :disabled="mentions.includes(m.im_user_id)" @click="mention(m.im_user_id)">
                  <ChatAvatar :name="m.nickname" size="sm" :status="m.online ? 'online' : null" />{{ m.nickname }}<span v-if="m.instance_id" class="chat-menu-hint">实例</span>
                </button>
              </div>
            </div>
            <label class="chat-input-field">
              <span class="sr-only">消息</span>
              <textarea ref="box" v-model="draft" rows="1" maxlength="4000" placeholder="输入消息…" :aria-describedby="`hint-${conversation.conversation_id}`" @keydown="keydown" />
            </label>
            <span :id="`hint-${conversation.conversation_id}`" class="sr-only">{{ wide ? 'Enter 发送，Shift + Enter 换行' : 'Ctrl + Enter 发送' }}</span>
            <input ref="picker" type="file" accept="image/png,image/jpeg,image/gif,image/webp" class="chat-file" aria-label="选择图片" @change="sendImage" />
            <button type="button" class="chat-icon-button" :disabled="busy" aria-label="发送图片" @click="picker?.click()"><Paperclip aria-hidden="true" /></button>
          </div>
          <button type="submit" class="chat-send" :disabled="busy || !draft.trim()" aria-label="发送"><Send aria-hidden="true" /></button>
        </div>
      </form>
      <p v-else class="chat-readonly">{{ dismissed ? '这个群已解散，只能查看记录。' : '系统通知会话只能查看。' }}</p>
    </div>
  </section>
</template>
