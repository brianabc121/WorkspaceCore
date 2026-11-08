<script setup lang="ts">
import { computed, ref } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { dataOf, errorMessage } from '../../services/api'
import { kbKey, useNote } from '../kb/api'

// 选一篇知识库笔记（T-0161）：按标题和正文搜索（与知识库的笔记列表同一个接口），不填关键词时列出最近的笔记
const props = defineProps<{ modelValue: string | null; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [id: string]; pick: [title: string] }>()
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id
const input = ref(''), q = ref('')
const results = useQuery({
  queryKey: computed(() => kbKey(user, scope, 'note-picker', q.value)),
  queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/knowledge/notes', { params: { header: { 'X-Workspace-Id': scope }, query: { limit: 20, ...(q.value ? { q: q.value } : {}) } }, signal })).items,
})
const selectedId = computed(() => props.modelValue)
const selected = useNote(scope, selectedId)
const picked = ref<string | null>(null)
const selectedTitle = computed(() => picked.value ?? selected.data.value?.title ?? null)
function search() { q.value = input.value.trim() }
function choose(note: { id: string; title: string }) {
  picked.value = note.title
  emit('update:modelValue', note.id)
  emit('pick', note.title)
}
</script>

<template>
  <fieldset class="note-picker" :disabled="disabled">
    <legend>笔记</legend>
    <p class="form-help">已选：<strong v-if="selectedTitle">{{ selectedTitle }}</strong><template v-else-if="modelValue && selected.error.value">（笔记不存在或已删除）</template><template v-else>还没有选</template></p>
    <div class="note-picker-search" role="search" aria-label="搜索笔记">
      <label class="form-field"><span>关键词</span><input v-model="input" type="search" maxlength="100" placeholder="笔记标题或正文里的词" @keydown.enter.prevent="search"></label>
      <button type="button" class="secondary-button" @click="search">搜索</button>
    </div>
    <p v-if="results.isPending.value" role="status">正在搜索…</p>
    <p v-else-if="results.error.value" role="alert">{{ errorMessage(results.error.value) }}</p>
    <p v-else-if="!results.data.value?.length" class="form-help">没有找到笔记。</p>
    <ul v-else class="note-picker-results" aria-label="笔记搜索结果">
      <li v-for="note in results.data.value" :key="note.id">
        <button type="button" class="secondary-button" :aria-pressed="note.id === modelValue" @click="choose(note)">{{ note.title }}<small>{{ note.space_name }}</small></button>
      </li>
    </ul>
  </fieldset>
</template>

<style scoped>
.note-picker { border: 1px solid var(--line); border-radius: 8px; padding: 8px 12px 12px; min-width: 0; margin: 12px 0; }
.note-picker legend { font-size: .85rem; font-weight: 600; padding: 0 4px; }
.note-picker-search { display: flex; flex-wrap: wrap; align-items: end; gap: 8px; }
.note-picker-search .form-field { flex: 1; min-width: 0; margin: 8px 0; }
.note-picker-results { list-style: none; margin: 8px 0 0; padding: 0; display: grid; gap: 6px; max-height: 240px; overflow-y: auto; }
.note-picker-results button { width: 100%; text-align: left; display: grid; gap: 2px; overflow-wrap: anywhere; }
.note-picker-results button[aria-pressed="true"] { border-color: var(--accent); background: var(--accent-soft); }
.note-picker-results small { color: var(--muted); font-size: .75rem; }
</style>
