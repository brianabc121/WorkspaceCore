<script setup lang="ts">
import { computed, ref, toRef } from 'vue'
import { formatTime } from '../../../features/time'
import { chatError, type ChatQueueItem, type LlmChatApi } from './chatApi'

// 会话的消息队列（docs/v2/LLM_BRIDGE_QUEUE_PLAN.md 第 4 节）：会话在处理时再发的消息排在这里，
// 前面的做完才发给模型。排队中的可以取消、改内容、调到最前面；已经开始处理的不能改。
const props = defineProps<{ chat: LlmChatApi; bridgeId: string; sessionId: string; busy: boolean }>()
const emit = defineEmits<{ changed: [] }>()

const query = props.chat.useQueue(toRef(props, 'bridgeId'), toRef(props, 'sessionId'), toRef(props, 'busy'))
const queued = computed(() => (props.busy ? query.data.value?.queued ?? [] : []))
const open = ref(true), working = ref(''), error = ref('')
const editing = ref<string | null>(null), draft = ref('')

async function act(turn: string, action: () => Promise<unknown>) {
  if (working.value) return
  working.value = turn; error.value = ''
  try { await action(); emit('changed') }
  catch (e) { error.value = chatError(e); void query.refetch() }
  finally { working.value = '' }
}
function startEdit(item: ChatQueueItem) { editing.value = item.turn_id; draft.value = item.text; error.value = '' }
function saveEdit(item: ChatQueueItem) {
  const text = draft.value.trim()
  if (!text) { error.value = '消息不能为空。'; return }
  return act(item.turn_id, async () => { await props.chat.editQueued(props.bridgeId, item.turn_id, text); editing.value = null })
}
const toFront = (item: ChatQueueItem) => act(item.turn_id, () => props.chat.moveQueued(props.bridgeId, item.turn_id, 'first'))
const cancel = (item: ChatQueueItem) => act(item.turn_id, async () => { await props.chat.cancel(props.bridgeId, item.turn_id); await query.refetch() })
</script>

<template>
  <section v-if="queued.length" class="lc-queue" aria-label="消息队列">
    <button type="button" class="lc-queue-head" :aria-expanded="open" @click="open = !open">队列里有 {{ queued.length }} 条消息：前面的做完后按顺序发给模型</button>
    <template v-if="open">
      <ol class="lc-queue-list">
        <li v-for="(item, index) in queued" :key="item.turn_id">
          <form v-if="editing === item.turn_id" class="lc-queue-edit" :aria-label="`修改排队中的第 ${index + 1} 条`" @submit.prevent="saveEdit(item)">
            <textarea v-model="draft" rows="2" maxlength="100000" aria-label="消息内容" />
            <button type="submit" class="primary-button" :disabled="!!working">保存</button>
            <button type="button" class="secondary-button" :disabled="!!working" @click="editing = null">不改了</button>
          </form>
          <template v-else>
            <p class="lc-queue-text"><span class="lc-queue-no">{{ index + 1 }}</span>{{ item.text }}</p>
            <p class="lc-queue-meta">
              {{ item.mine ? '我' : item.by || '别人' }} · {{ formatTime(new Date(item.queued_at * 1000).toISOString()) }}
              <button v-if="index > 0" type="button" class="asv-link" :disabled="!!working" :aria-label="`把第 ${index + 1} 条调到最前面`" @click="toFront(item)">调到最前面</button>
              <button type="button" class="asv-link" :disabled="!!working" :aria-label="`修改第 ${index + 1} 条`" @click="startEdit(item)">修改</button>
              <button type="button" class="asv-link lb-danger" :disabled="!!working" :aria-label="`取消第 ${index + 1} 条`" @click="cancel(item)">取消</button>
            </p>
          </template>
        </li>
      </ol>
      <p v-if="error" class="lc-note is-red" role="alert">{{ error }}</p>
    </template>
  </section>
</template>
