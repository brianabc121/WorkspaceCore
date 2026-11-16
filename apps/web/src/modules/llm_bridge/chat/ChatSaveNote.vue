<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import ModalPanel from '../../../components/ModalPanel.vue'
import { chatError, type LlmChatApi } from './chatApi'

// 把一条回答存进知识库（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 5.2 第二层“正式笔记”）：
// 人选知识库、改标题和内容后保存，走人的权限和知识库现有的检查（密钥拦截、大小、版本）。
// 保存前用标题在同一个知识库里搜一下：有相近的就问“接在它后面，还是另存一篇”。
// 内容末尾带出处（哪个会话、哪一回合），每条都能点回去。
const props = defineProps<{ chat: LlmChatApi; question: string; answer: string; sessionTitle: string; link: string }>()
const emit = defineEmits<{ close: [] }>()

type Space = { id: string; name: string; kind: string }
type Similar = { id: string; title: string }
const spaces = ref<Space[]>([]), space = ref(''), title = ref(''), body = ref('')
const similar = ref<Similar[] | null>(null), busy = ref(false), error = ref(''), saved = ref<{ id: string; title: string } | null>(null)
onMounted(async () => {
  title.value = props.question.split('\n').map(l => l.trim()).find(Boolean)?.slice(0, 80) ?? '大模型的回答'
  body.value = `${props.answer.trim()}\n\n> 出处：会话[《${props.sessionTitle || '没有标题'}》](${props.link})`
  try {
    spaces.value = (await props.chat.kbSpaces()).filter(s => !s.archived).map(s => ({ id: s.id, name: s.name, kind: s.kind }))
    space.value = spaces.value.find(s => s.kind === 'public')?.id ?? spaces.value[0]?.id ?? ''
  } catch (e) { error.value = chatError(e) }
})
const ready = computed(() => !!space.value && !!title.value.trim() && !!body.value.trim())
const REASON = '从大模型会话的回答存入'
async function save(force = false) {
  if (!ready.value || busy.value) return
  busy.value = true; error.value = ''
  try {
    if (!force && similar.value === null) {
      const found = (await props.chat.kbSimilar(space.value, title.value.trim())).map(n => ({ id: n.id, title: n.title }))
      if (found.length) { similar.value = found; return }
    }
    const note = await props.chat.kbCreate({ space_id: space.value, title: title.value.trim(), body: body.value, type: 'record', tags: [], folder: '', aliases: [], pinned: false, reason: REASON })
    saved.value = { id: note.id, title: note.title }
  } catch (e) { error.value = chatError(e) } finally { busy.value = false }
}
async function append(target: Similar) {
  if (busy.value) return
  busy.value = true; error.value = ''
  try { await props.chat.kbAppend(target.id, body.value, REASON); saved.value = target }
  catch (e) { error.value = chatError(e) } finally { busy.value = false }
}
</script>

<template>
  <ModalPanel title="存进知识库" :busy="busy" @close="emit('close')">
    <div v-if="saved" class="lc-save-done" role="status">
      <p>已存进《{{ saved.title }}》。</p>
      <p><RouterLink class="asv-link" :to="`/knowledge/notes/${saved.id}`">打开这篇笔记</RouterLink></p>
      <button type="button" class="primary-button" @click="emit('close')">完成</button>
    </div>
    <form v-else class="asv-form lc-save" aria-label="存进知识库" @submit.prevent="save()">
      <p class="form-help">存下来的是人写的笔记，和知识库里别的笔记一样：可以改、有版本、删除进回收站。内容里有密钥会被拦下。</p>
      <div class="form-field">
        <label for="lc-save-space">知识库</label>
        <select id="lc-save-space" v-model="space" :disabled="busy" @change="similar = null">
          <option v-for="s in spaces" :key="s.id" :value="s.id">{{ s.name }}</option>
        </select>
      </div>
      <div class="form-field">
        <label for="lc-save-title">标题</label>
        <input id="lc-save-title" v-model="title" maxlength="200" :disabled="busy" @input="similar = null" />
      </div>
      <div class="form-field">
        <label for="lc-save-body">内容</label>
        <textarea id="lc-save-body" v-model="body" rows="10" :disabled="busy" />
      </div>
      <div v-if="similar?.length" class="lc-note" role="note">
        <p>这个知识库里已有相近的笔记：可以把这段接在它后面，或者另存一篇。</p>
        <ul class="lc-kb-list">
          <li v-for="n in similar" :key="n.id">
            <RouterLink class="asv-link" :to="`/knowledge/notes/${n.id}`">《{{ n.title }}》</RouterLink>
            <button type="button" class="secondary-button" :disabled="busy" @click="append(n)">接在它后面</button>
          </li>
        </ul>
        <button type="button" class="secondary-button" :disabled="busy || !ready" @click="save(true)">另存一篇</button>
      </div>
      <p v-if="error" class="lc-note is-red" role="alert">{{ error }}</p>
      <div v-if="!similar?.length" class="lc-save-actions">
        <button type="submit" class="primary-button" :disabled="busy || !ready">{{ busy ? '正在保存…' : '保存' }}</button>
        <button type="button" class="secondary-button" :disabled="busy" @click="emit('close')">取消</button>
      </div>
    </form>
  </ModalPanel>
</template>
