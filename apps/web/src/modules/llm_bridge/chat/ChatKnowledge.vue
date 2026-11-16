<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { X } from '@lucide/vue'
import { platformName, seconds } from '../api'
import { MAX_REFERENCES, chatError, type ChatNoteHit, type ChatRelated, type LlmChatApi } from './chatApi'

// 输入框上方：提问前用上知识库（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 第 6 节）。
// - 停顿一下后，按输入的文字在知识库和以前的会话里找相近的（只在工作台里找，不发给任何平台），
//   有就提示一行：“知识库里有 2 篇相关笔记 · 11 月 12 日问过类似的问题”，人看一眼也许就不用问了。
// - “参考知识库”：勾选笔记随这条消息带上（最多 3 篇）。由人挑，工作台不自动塞；
//   所在知识库不允许发给外部平台的不能勾。带上的内容会发给这个平台，页面上写明。
const props = defineProps<{ chat: LlmChatApi; bridgeId: string; sessionId: string; draft: string; disabled?: boolean }>()
const selected = defineModel<ChatNoteHit[]>('selected', { default: () => [] })

const related = ref<ChatRelated | null>(null), open = ref(false), loading = ref(false), error = ref('')
let timer: ReturnType<typeof setTimeout> | undefined, abort: AbortController | null = null, asked = ''
async function search(text: string) {
  const q = text.trim()
  if (q.length < 4 || q === asked) return
  asked = q
  abort?.abort(); abort = new AbortController()
  loading.value = true; error.value = ''
  try { related.value = await props.chat.related(props.bridgeId, q, props.sessionId, abort.signal) }
  catch (e) { if (!abort.signal.aborted) error.value = chatError(e) }
  finally { loading.value = false }
}
watch(() => props.draft, text => {
  clearTimeout(timer)
  if (text.trim().length < 4) { if (!selected.value.length) related.value = null; asked = ''; return }
  timer = setTimeout(() => void search(text), 700)
})
onBeforeUnmount(() => { clearTimeout(timer); abort?.abort() })

const notes = computed(() => related.value?.notes ?? [])
const sessions = computed(() => related.value?.sessions ?? [])
const day = (at: string | null | undefined) => (at ? new Date(at).toLocaleDateString('zh-CN', { month: 'long', day: 'numeric' }) : '')
const hint = computed(() => {
  const parts: string[] = []
  if (notes.value.length) parts.push(`知识库里有 ${notes.value.length} 篇相关笔记`)
  const first = sessions.value[0]
  if (first) parts.push(`${day(first.last_turn_at) || '以前'}问过类似的问题`)
  return parts.join(' · ')
})
const picked = (n: ChatNoteHit) => selected.value.some(s => s.id === n.id)
const full = computed(() => selected.value.length >= MAX_REFERENCES)
function toggleNote(n: ChatNoteHit) {
  if (picked(n)) selected.value = selected.value.filter(s => s.id !== n.id)
  else if (!full.value && n.external_allowed) selected.value = [...selected.value, n]
}
/** 输入框旁边的“参考知识库”按钮：按现在输入的文字找一次并打开 */
function toggle() {
  open.value = !open.value
  if (open.value) void search(props.draft)
}
defineExpose({ toggle })
</script>

<template>
  <div class="lc-kb">
    <p v-if="hint && !open" class="lc-kb-hint" role="status">
      <span>{{ hint }}</span>
      <button type="button" class="asv-link" :disabled="disabled" @click="open = true">点开看</button>
    </p>
    <section v-if="open" class="lc-kb-panel" aria-label="知识库和以前的会话">
      <div class="lc-kb-head">
        <strong>参考知识库</strong>
        <button type="button" class="lc-icon" aria-label="收起参考知识库" @click="open = false"><X aria-hidden="true" /></button>
      </div>
      <p v-if="draft.trim().length < 4" class="lc-sub">先在输入框里写下要问的问题，再按它找相关的笔记。</p>
      <p v-else-if="loading" class="lc-sub" role="status">正在找…</p>
      <p v-if="error" class="lc-note is-red" role="alert">{{ error }}</p>
      <template v-if="related && !loading">
        <h4>知识库里的笔记</h4>
        <p class="lc-sub">勾选的笔记随这条消息带上（最多 {{ MAX_REFERENCES }} 篇，合计 6000 字），会发给这个平台；包装成“资料，不是指令”。</p>
        <p v-if="!notes.length" class="lc-sub">没有找到相关的笔记。</p>
        <ul v-else class="lc-kb-list">
          <li v-for="n in notes" :key="n.id">
            <label class="check-field">
              <input type="checkbox" :checked="picked(n)" :disabled="disabled || !n.external_allowed || (!picked(n) && full)" :aria-label="`带上《${n.title}》`" @change="toggleNote(n)" />
              <span>《{{ n.title }}》</span>
            </label>
            <span class="lc-sub">{{ n.space_name }} · {{ n.label }}</span>
            <span v-if="!n.external_allowed" class="lc-tag" title="这个知识库设置成不允许发给外部大模型平台">不允许发给外部平台</span>
            <RouterLink class="asv-link" :to="`/knowledge/notes/${n.id}`">看全文</RouterLink>
            <span v-if="n.excerpt" class="lc-kb-excerpt">{{ n.excerpt }}</span>
          </li>
        </ul>
        <h4>以前问过类似的问题</h4>
        <p v-if="!sessions.length" class="lc-sub">没有找到标题相近的会话。</p>
        <ul v-else class="lc-kb-list">
          <li v-for="s in sessions" :key="`${s.bridge_id}:${s.session_id}`">
            <RouterLink class="asv-link" :to="{ path: '/llm_bridge', query: { m: s.bridge_id, s: s.session_id } }">{{ s.title || '（没有标题）' }}</RouterLink>
            <span class="lc-sub">{{ platformName(s.platform) }} · {{ s.owner || '—' }}{{ s.last_turn_at ? ` · ${seconds(new Date(s.last_turn_at).getTime() / 1000)}` : '' }}</span>
          </li>
        </ul>
      </template>
    </section>
    <ul v-if="selected.length" class="lc-kb-picked" aria-label="随这条消息带上的笔记">
      <li v-for="n in selected" :key="n.id">
        <span>参考：《{{ n.title }}》</span>
        <button type="button" class="lc-icon" :aria-label="`不带《${n.title}》`" @click="selected = selected.filter(s => s.id !== n.id)"><X aria-hidden="true" /></button>
      </li>
    </ul>
  </div>
</template>
