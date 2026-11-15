<script setup lang="ts">
import { computed, ref, toRef } from 'vue'
import { Archive, ArrowLeft, Send } from '@lucide/vue'
import DataState from '../../../components/DataState.vue'
import { platformName } from '../api'
import { chatError, needsTakeover, newRequestId, type ColumnSend, type LlmChatApi } from './chatApi'
import ChatCompareColumn from './ChatCompareColumn.vue'

// 多模型同问（docs/v2/LLM_CHAT_PLAN.md 第 3.4 节）：一组里每个模型一栏，并排看回答；下面的输入框向每一栏继续发。
// 父组件按分组给 key，换一组时整个重建。窄屏时一次看一栏，用上面的标签切换（每一栏都在读自己的进度）。
const props = defineProps<{ chat: LlmChatApi; groupId: string; narrow: boolean }>()
const emit = defineEmits<{ back: []; changed: [] }>()

const group = props.chat.useGroup(toRef(props, 'groupId'))
const data = computed(() => group.data.value ?? null)
const members = computed(() => data.value?.members ?? [])
const sendable = computed(() => members.value.filter(m => m.session_id && m.bridge_id && m.bridge_enabled).length)
const shown = ref(0)

const draft = ref(''), sending = ref(false), error = ref(''), working = ref(false), confirmed = ref(false)
const latest = ref<Record<number, ColumnSend>>({})
let pending: { text: string; id: string } | null = null, sequence = 0
const foreign = computed(() => !!data.value && data.value.owner_kind !== 'me')
const blocked = computed(() => {
  if (!data.value) return ''
  if (data.value.status === 'archived') return '这一组同问已归档，不能再一起发；每个模型的会话还在，可以单独继续。'
  return sendable.value ? '' : '这一组里没有能继续发的会话（会话没建成，或者 MCP 管理器已停用、已删除）。'
})
function allowTakeover() {
  if (confirmed.value) return true
  confirmed.value = window.confirm(`这一组同问是「${data.value?.owner || '别的管理员'}」建的。接着发问题会进入每个模型原来的会话。\n\n确定要接着发吗？`)
  return confirmed.value
}
async function submit() {
  const text = draft.value.trim()
  if (!text || sending.value || blocked.value) return
  if (foreign.value && !allowTakeover()) return
  if (pending?.text !== text) pending = { text, id: newRequestId() }
  const body = { text, request_id: pending.id }
  sending.value = true; error.value = ''
  try {
    let out
    try { out = await props.chat.sendGroup(props.groupId, { ...body, takeover: foreign.value }) }
    catch (e) {
      if (!needsTakeover(e) || !allowTakeover()) throw e
      out = await props.chat.sendGroup(props.groupId, { ...body, takeover: true })
    }
    pending = null; draft.value = ''
    const id = ++sequence
    latest.value = Object.fromEntries(out.results.map(r => [r.position, { id, text, turn: r.turn ?? null, error: r.error ?? '' }]))
    emit('changed')
  } catch (e) {
    error.value = needsTakeover(e) ? '没有发送：接着别人建的一组发问题前需要确认。' : chatError(e)
  } finally { sending.value = false }
}
function onKey(event: KeyboardEvent) {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return
  event.preventDefault()
  void submit()
}
async function archive() {
  if (working.value || !window.confirm('归档这一组同问？\n\n只是不再把它们并排显示；每个模型的会话不受影响，还能单独继续。')) return
  working.value = true; error.value = ''
  try { await props.chat.archiveGroup(props.groupId); emit('changed') }
  catch (e) { error.value = chatError(e) }
  finally { working.value = false }
}
</script>

<template>
  <div class="lc-compare">
    <header class="lc-head">
      <button v-if="narrow" type="button" class="lc-icon" aria-label="返回会话列表" @click="emit('back')"><ArrowLeft aria-hidden="true" /></button>
      <div class="lc-head-main">
        <h2>{{ data ? data.title || '（没有标题）' : '多模型同问' }}</h2>
        <p v-if="data" class="lc-head-meta">
          同一个问题同时发给 {{ members.length }} 个模型，并排看回答 · {{ data.owner_kind === 'me' ? '我' : data.owner || '别的管理员' }}建的
          <span v-if="data.status === 'archived'" class="lc-tag">已归档</span>
        </p>
      </div>
      <div v-if="data && data.status !== 'archived'" class="lc-head-actions">
        <button type="button" class="lc-icon" aria-label="归档这一组" title="归档这一组" :disabled="working" @click="archive"><Archive aria-hidden="true" /></button>
      </div>
    </header>
    <DataState v-if="group.isPending.value || (group.error.value && !data)" :loading="group.isPending.value" :error="group.error.value" :empty="false" empty-text="" @retry="group.refetch()" />
    <template v-else-if="data">
      <div v-if="narrow && members.length > 1" class="lc-col-tabs" role="tablist" aria-label="这一组里的模型">
        <button v-for="m in members" :key="m.position" type="button" role="tab" :aria-selected="shown === m.position" @click="shown = m.position">{{ platformName(m.platform) }} · {{ m.bridge_name }}</button>
      </div>
      <div class="lc-cols">
        <ChatCompareColumn v-for="m in members" v-show="!narrow || shown === m.position" :key="m.position" :chat="chat" :member="m" :latest="latest[m.position] ?? null" />
      </div>
      <form class="lc-composer" aria-label="向这一组继续发" @submit.prevent="submit">
        <p v-if="blocked" class="lc-note" role="note">{{ blocked }}</p>
        <template v-else>
          <p v-if="foreign" class="lc-note" role="note">这一组是「{{ data.owner || '别的管理员' }}」建的：可以看；接着发问题前会让你确认一次。</p>
          <div class="lc-compose-row">
            <textarea v-model="draft" rows="2" maxlength="100000" aria-label="问题内容" :placeholder="`继续问这 ${sendable} 个模型：Enter 发送，Shift + Enter 换行`" @keydown="onKey" />
            <button type="submit" class="primary-button" :disabled="sending || !draft.trim()"><Send aria-hidden="true" />{{ sending ? '正在发送…' : `发给 ${sendable} 个模型` }}</button>
          </div>
          <p v-if="sendable < members.length" class="lc-hint">有 {{ members.length - sendable }} 个不能继续发（会话没建成，或者它的 MCP 管理器已停用、已删除）。</p>
        </template>
        <p v-if="error" class="lc-note is-red" role="alert">{{ error }}</p>
      </form>
    </template>
  </div>
</template>
