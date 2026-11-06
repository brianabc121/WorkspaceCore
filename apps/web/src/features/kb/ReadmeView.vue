<script setup lang="ts">
// 项目或任务的 README（T-0122，方案 9.7）：渲染 Markdown；工作台自动更新的部分带“自动更新”角标；
// “编辑”打开知识库编辑器（README 就是项目知识库里的一篇 readme 笔记）。
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import type { Readme } from './api'
import { noteHtml } from './format'
import { formatTime } from '../time'
import './kb.css'

const props = defineProps<{ readme: Readme; writable: boolean }>()
const router = useRouter()
// 正文里的站内链接用路由跳转，不整页刷新
function follow(event: MouseEvent | KeyboardEvent) {
  const link = (event.target as HTMLElement | null)?.closest('a')
  const href = link?.getAttribute('href')
  if (!href || !href.startsWith('/') || event.ctrlKey || event.metaKey || event.shiftKey) return
  event.preventDefault()
  void router.push(href)
}
const BLOCK = /<!-- myws:auto ([^<>\n]+?) -->\n?([\s\S]*?)\n?<!-- \/myws:auto -->/g
const chunks = computed(() => {
  const note = props.readme.note, body = note.body, out: { auto: string | null; html: string }[] = []
  let cursor = 0
  for (const match of body.matchAll(BLOCK)) {
    if (match.index! > cursor) out.push({ auto: null, html: noteHtml({ ...note, body: body.slice(cursor, match.index) }) })
    out.push({ auto: match[1].trim(), html: noteHtml({ ...note, body: match[2] }) })
    cursor = match.index! + match[0].length
  }
  if (cursor < body.length) out.push({ auto: null, html: noteHtml({ ...note, body: body.slice(cursor) }) })
  return out
})
</script>

<template>
  <section class="readme" aria-label="README">
    <div class="readme-bar">
      <span class="form-help">知识库里的 README · 第 {{ readme.note.version }} 版 · 更新于 {{ formatTime(readme.note.updated_at) }}</span>
      <span class="form-actions">
        <RouterLink :to="`/knowledge/notes/${readme.note.id}`">在知识库中查看</RouterLink>
        <RouterLink v-if="writable" class="secondary-button" :to="`/knowledge/notes/${readme.note.id}/edit`">编辑</RouterLink>
      </span>
    </div>
    <article class="kb-md" @click="follow" @keydown.enter="follow">
      <template v-for="(chunk, index) in chunks" :key="index">
        <div v-if="chunk.auto" class="readme-auto" :aria-label="`自动更新：${chunk.auto}`">
          <span class="badge readme-badge">自动更新</span>
          <!-- eslint-disable-next-line vue/no-v-html -- noteHtml 已用 DOMPurify 消毒 -->
          <div v-html="chunk.html"></div>
        </div>
        <!-- eslint-disable-next-line vue/no-v-html -- noteHtml 已用 DOMPurify 消毒 -->
        <div v-else v-html="chunk.html"></div>
      </template>
    </article>
  </section>
</template>

<style scoped>
.readme { display: flex; flex-direction: column; gap: 12px; }
.readme-bar { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; }
.readme-auto { position: relative; padding: 8px 12px; border-left: 3px solid var(--line); background: var(--surface-muted, transparent); border-radius: 4px; }
.readme-badge { float: right; margin-left: 8px; font-size: 12px; }
</style>
