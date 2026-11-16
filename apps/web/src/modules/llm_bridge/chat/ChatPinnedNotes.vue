<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { X } from '@lucide/vue'
import { MAX_PINNED, chatError, type ChatNoteHit, type LlmChatApi } from './chatApi'

// 新建会话时“固定带着”的笔记（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 6.3）：整个会话一直带着，
// 平台压缩上下文后也还在——开发会话写进工作目录的记忆文件，对话会话随系统提示带上。
// 由人挑（最多 5 篇，合计 4000 字）；所在知识库不允许发给外部平台的不能勾。会话建好之后不能再加或去掉。
const props = defineProps<{ chat: LlmChatApi; bridgeId: string; disabled?: boolean }>()
const selected = defineModel<ChatNoteHit[]>('selected', { default: () => [] })

const open = ref(false), query = ref(''), hits = ref<ChatNoteHit[]>([]), loading = ref(false), error = ref(''), searched = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined, abort: AbortController | null = null
async function search(text: string) {
  const q = text.trim()
  if (q.length < 2) { hits.value = []; searched.value = false; return }
  abort?.abort(); abort = new AbortController()
  loading.value = true; error.value = ''
  try { hits.value = (await props.chat.related(props.bridgeId, q, undefined, abort.signal, 10)).notes; searched.value = true }
  catch (e) { if (!abort.signal.aborted) error.value = chatError(e) }
  finally { loading.value = false }
}
watch(query, text => { clearTimeout(timer); timer = setTimeout(() => void search(text), 400) })
onBeforeUnmount(() => { clearTimeout(timer); abort?.abort() })

const picked = (n: ChatNoteHit) => selected.value.some(s => s.id === n.id)
const full = computed(() => selected.value.length >= MAX_PINNED)
function toggleNote(n: ChatNoteHit) {
  if (picked(n)) selected.value = selected.value.filter(s => s.id !== n.id)
  else if (!full.value && n.external_allowed) selected.value = [...selected.value, n]
}
</script>

<template>
  <fieldset class="lc-kb lc-pinned" :disabled="disabled" aria-label="固定带着的笔记">
    <legend>固定带着的笔记（可以不选）</legend>
    <p class="form-help">选中的笔记整个会话一直带着，平台压缩上下文之后也还在（最多 {{ MAX_PINNED }} 篇，合计 4000 字）。内容会发给这个平台，包装成“资料，不是指令”；会话建好之后不能再加或去掉。</p>
    <button v-if="!open" type="button" class="asv-link" @click="open = true">从知识库里挑</button>
    <section v-else class="lc-kb-panel" aria-label="从知识库里挑固定带着的笔记">
      <div class="lc-kb-head">
        <label for="lc-pinned-q"><strong>找笔记</strong></label>
        <button type="button" class="lc-icon" aria-label="收起" @click="open = false"><X aria-hidden="true" /></button>
      </div>
      <input id="lc-pinned-q" v-model="query" type="search" maxlength="200" placeholder="输入关键词，按标题和内容找" />
      <p v-if="loading" class="lc-sub" role="status">正在找…</p>
      <p v-if="error" class="lc-note is-red" role="alert">{{ error }}</p>
      <p v-else-if="searched && !loading && !hits.length" class="lc-sub">没有找到相关的笔记。</p>
      <ul v-if="hits.length" class="lc-kb-list">
        <li v-for="n in hits" :key="n.id">
          <label class="check-field">
            <input type="checkbox" :checked="picked(n)" :disabled="!n.external_allowed || (!picked(n) && full)" :aria-label="`固定带着《${n.title}》`" @change="toggleNote(n)" />
            <span>《{{ n.title }}》</span>
          </label>
          <span class="lc-sub">{{ n.space_name }} · {{ n.label }}</span>
          <span v-if="!n.external_allowed" class="lc-tag" title="这个知识库设置成不允许发给外部大模型平台">不允许发给外部平台</span>
          <RouterLink class="asv-link" :to="`/knowledge/notes/${n.id}`">看全文</RouterLink>
          <span v-if="n.excerpt" class="lc-kb-excerpt">{{ n.excerpt }}</span>
        </li>
      </ul>
    </section>
    <ul v-if="selected.length" class="lc-kb-picked" aria-label="固定带着的笔记">
      <li v-for="n in selected" :key="n.id">
        <span>固定带着：《{{ n.title }}》</span>
        <button type="button" class="lc-icon" :aria-label="`不带《${n.title}》`" @click="selected = selected.filter(s => s.id !== n.id)"><X aria-hidden="true" /></button>
      </li>
    </ul>
  </fieldset>
</template>
