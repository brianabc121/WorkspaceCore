<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { dataOf, type Schema } from '../../services/api'
import { chronological, imError, useContacts, useImStatus, useMessages } from './queries'
import { Button } from '@/components/ui/button'

// 右侧抽屉的“AI 助手”（规范 §8.1；V2 里 AI 就是经 IM 对话的 OpenClaw 实例）：选一个实例，看最近的对话并发消息。
// 走工作台的 IM 接口，浏览器不直连 OpenIM；只对 owner / admin 开放（服务端校验，这里只说明）。
const auth = useAuthStore(), cache = useQueryClient()
const scope = auth.workspaceId!, user = auth.user?.id
const header = { 'X-Workspace-Id': scope }
const status = useImStatus(scope), contacts = useContacts(scope)
const instances = computed(() => (contacts.data.value?.items ?? []).filter(c => c.instance_id && c.status !== 'disabled'))
const KEY = `workspace-assistant-instance:${scope}`
function remembered() { try { return localStorage.getItem(KEY) ?? '' } catch { return '' } }
const instanceId = ref(remembered())
const conversationId = ref<string | null>(null)
const messages = useMessages(scope, conversationId)
const items = computed(() => chronological(messages.data.value?.pages).slice(-30))
const draft = ref(''), busy = ref(false), error = ref(''), list = ref<HTMLElement | null>(null)
const names = computed<Record<string, string>>(() => Object.fromEntries((contacts.data.value?.items ?? []).map(c => [c.im_user_id, c.nickname])))
// 一份草稿一个幂等键：结果未知后重试同一份草稿不会发两次
let draftKey: string | null = null
watch(draft, () => { draftKey = null })
const active = () => auth.user?.id === user && auth.workspaceId === scope

watch(instances, (list) => {
  if (instanceId.value && !list.some(c => c.instance_id === instanceId.value)) instanceId.value = ''
  if (!instanceId.value && list.length === 1) instanceId.value = list[0]!.instance_id!
}, { immediate: true })
watch(instanceId, async (id) => {
  conversationId.value = null
  error.value = ''
  try { localStorage.setItem(KEY, id) } catch { /* 只在内存里记住 */ }
  if (!id || !auth.canManage) return
  try {
    const conversation = dataOf(await auth.api.POST('/api/v1/im/conversations', { params: { header }, body: { instance_id: id } }))
    if (active() && instanceId.value === id) conversationId.value = conversation.conversation_id
  } catch (cause) { if (active()) error.value = imError(cause) }
}, { immediate: true })
watch(() => items.value.length, async () => { await nextTick(); if (list.value) list.value.scrollTop = list.value.scrollHeight })

function sender(message: Schema['ImMessageOut']) {
  if (message.sender_im_id === status.data.value?.im_user_id) return '我'
  return names.value[message.sender_im_id] ?? message.sender_im_id
}
function text(message: Schema['ImMessageOut']) {
  if (message.kind === 'image') return '[图片]'
  if (message.kind === 'approval_card') return '[审批卡片]'
  return message.text ?? ''
}
async function send() {
  const body = draft.value.trim()
  if (!body || !conversationId.value || busy.value) return
  busy.value = true; error.value = ''
  draftKey ??= crypto.randomUUID()
  try {
    dataOf(await auth.api.POST('/api/v1/im/conversations/{conversation_id}/messages', { params: { header: { ...header, 'Idempotency-Key': draftKey }, path: { conversation_id: conversationId.value } }, body: { kind: 'text', text: body, at_im_ids: [] } }))
    if (!active()) return
    draft.value = ''; draftKey = null
    await cache.invalidateQueries({ predicate: q => q.queryKey[0] === 'im' && q.queryKey[1] === user && q.queryKey[2] === scope })
  } catch (cause) { if (active()) error.value = imError(cause) }
  finally { busy.value = false }
}
</script>

<template>
  <div class="assistant-chat">
    <p v-if="!auth.canManage" class="card-state">AI 助手是与 OpenClaw 实例的 IM 对话，只对 owner 与 admin 开放。</p>
    <p v-else-if="status.isPending.value || contacts.isPending.value" role="status" class="card-state">正在连接 IM…</p>
    <p v-else-if="status.error.value" role="alert" class="card-state">{{ imError(status.error.value) }}</p>
    <p v-else-if="!status.data.value?.available" class="card-state">还没有部署 OpenIM，AI 助手暂不可用（docs/deploy-guide.md 第 8.6 步）。</p>
    <p v-else-if="!instances.length" class="card-state">还没有可对话的实例。<RouterLink to="/agents">登记 OpenClaw 实例 →</RouterLink></p>
    <template v-else>
      <label class="form-field"><span>对话的实例</span>
        <select v-model="instanceId" :disabled="busy"><option value="" disabled>选择实例</option><option v-for="c in instances" :key="c.im_user_id" :value="c.instance_id!">{{ c.nickname }}（{{ c.status === 'online' ? '在线' : '离线' }}）</option></select>
      </label>
      <div ref="list" class="assistant-messages" aria-live="polite" aria-label="最近的对话">
        <p v-if="!conversationId" class="card-state">{{ instanceId ? '正在打开会话…' : '选择一个实例开始对话。' }}</p>
        <p v-else-if="messages.isPending.value" role="status" class="card-state">正在加载消息…</p>
        <p v-else-if="!items.length" class="card-state">还没有消息，发一句试试。</p>
        <ol v-else>
          <li v-for="m in items" :key="m.server_msg_id ?? m.client_msg_id" :class="{ 'is-mine': m.sender_im_id === status.data.value?.im_user_id }">
            <span class="assistant-sender">{{ sender(m) }}</span>
            <span class="preserve-text">{{ text(m) }}</span>
          </li>
        </ol>
      </div>
      <form class="assistant-composer" aria-label="发消息给实例" @submit.prevent="send">
        <label class="form-field"><span>消息</span><textarea v-model="draft" rows="3" maxlength="4000" placeholder="问实例一个问题，Ctrl + Enter 发送" :disabled="!conversationId" @keydown.ctrl.enter.prevent="send" @keydown.meta.enter.prevent="send" /></label>
        <p v-if="error" role="alert">{{ error }}</p>
        <div class="form-actions">
          <Button type="submit" size="sm" :disabled="busy || !conversationId || !draft.trim()">{{ busy ? '正在发送…' : '发送' }}</Button>
          <RouterLink v-if="conversationId" class="primary-link" :to="{ path: '/im', query: { c: conversationId } }">在消息页打开 →</RouterLink>
        </div>
      </form>
    </template>
  </div>
</template>
