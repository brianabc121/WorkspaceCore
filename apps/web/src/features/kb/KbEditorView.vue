<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, shallowRef, watch } from 'vue'
import { RouterLink, onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { Bold, CircleAlert, Code, Heading, Italic, List, ListChecks, ListOrdered, Quote, Redo2, SlidersHorizontal, Strikethrough, Table, Undo2, X } from '@lucide/vue'
import type { EditorView } from '@codemirror/view'
import { useAuthStore } from '../../stores/auth'
import { ApiError } from '../../services/api'
import ModalPanel from '../../components/ModalPanel.vue'
import { useMediaQuery } from '../../composables/useMediaQuery'
import { EDITABLE_TYPES, NOTE_TYPE_LABELS, SPACE_KIND_LABELS, authorLabel, kbError, useKb, useNote, useNotes, useSpaces, type Note, type NoteType } from './api'
import { createEditor, insertBlock, insertText, linePrefix, runRedo, runUndo, scrollToLine, setDoc, wrap, type Suggestion } from './cm'
import { createRenderer, inlineTags, normalize, outline, stats } from './markdown'
import { assemble, conflicts, threeWay, unresolved, type MergeChunk } from './merge'
import { when } from './format'
import KbMobileHeader from './KbMobileHeader.vue'

// 笔记编辑器（T-0099，效果图“电脑端 · 笔记编辑器”）：编辑 / 分屏 / 阅读三种方式；工具栏；[[ 与 # 补全；
// 草稿自动存在本机（localStorage）；保存时带上开始编辑时的版本，别人先保存了就提示并可以三方合并。
const auth = useAuthStore(), route = useRoute(), router = useRouter(), scope = auth.workspaceId!
const kb = useKb(scope)
const canEdit = computed(() => ['owner', 'admin', 'member'].includes(auth.currentWorkspace?.role ?? ''))
const noteId = computed(() => typeof route.params.id === 'string' ? route.params.id : null)
const isNew = computed(() => !noteId.value)
const source = useNote(scope, noteId)
const spaces = useSpaces(scope)
const spaceItems = computed(() => spaces.data.value?.items ?? [])

const title = ref(''), body = ref(''), reason = ref('')
const fields = reactive({ space_id: '', type: 'record' as NoteType, tags: [] as string[], aliases: [] as string[], folder: '', review_at: '', pinned: false })
const base = ref<{ version: number; title: string; body: string } | null>(null)
const loaded = ref(false), saving = ref(false), failure = ref(''), notice = ref('')
const snapshot = ref('')
const state = () => JSON.stringify({ t: title.value, b: body.value, f: fields })
const dirty = computed(() => loaded.value && state() !== snapshot.value)

// 编辑方式：宽屏默认分屏，窄屏默认编辑；记在本机
const wide = useMediaQuery('(min-width: 1100px)')
const MODES = [{ id: 'edit', label: '编辑' }, { id: 'split', label: '分屏' }, { id: 'read', label: '阅读' }] as const
type Mode = typeof MODES[number]['id']
const storedMode = (() => { try { return localStorage.getItem('kb-editor-mode') as Mode | null } catch { return null } })()
const mode = ref<Mode>(storedMode && MODES.some(m => m.id === storedMode) ? storedMode : wide.value ? 'split' : 'edit')
watch(mode, (m) => { try { localStorage.setItem('kb-editor-mode', m) } catch { /* 只在本次有效 */ } })

// ---------------------------------------------------------------- 初始化
function load(note: Note | null) {
  if (note) {
    title.value = note.title; body.value = note.body
    Object.assign(fields, { space_id: note.space_id, type: note.type, tags: [...note.tags], aliases: [...note.aliases], folder: note.folder, review_at: note.review_at ?? '', pinned: note.pinned })
    base.value = { version: note.version, title: note.title, body: note.body }
  } else {
    const fromQuery = typeof route.query.space === 'string' ? route.query.space : ''
    const fallback = spaceItems.value.find(s => s.kind === 'public')?.id ?? spaceItems.value[0]?.id ?? ''
    fields.space_id = spaceItems.value.some(s => s.id === fromQuery) ? fromQuery : fallback
    title.value = typeof route.query.title === 'string' ? route.query.title.slice(0, 200) : ''
    if (route.query.type === 'brief' && spaceItems.value.find(s => s.id === fields.space_id)?.kind === 'project') fields.type = 'brief'
  }
  snapshot.value = state()
  loaded.value = true
  restoreOffer()
  void nextTick(mountEditor)
}
watch([() => source.data.value, () => spaces.data.value], () => {
  if (loaded.value || !spaces.data.value) return
  if (isNew.value) load(null)
  else if (source.data.value) load(source.data.value)
}, { immediate: true })
// 顶栏“创建 → 新建笔记”带来的 ?create=1：页面本身就是新建，去掉参数即可
if (route.query.create !== undefined) { const q = { ...route.query }; delete q.create; void router.replace({ query: q }) }

// ---------------------------------------------------------------- 草稿
const draftKey = computed(() => `kb-draft:${auth.user?.id}:${scope}:${noteId.value ?? 'new'}`)
type Draft = { t: string; b: string; f: typeof fields; v: number | null; at: string }
const draftOffer = ref<Draft | null>(null), draftAt = ref('')
function restoreOffer() {
  try {
    const raw = localStorage.getItem(draftKey.value)
    const draft = raw ? JSON.parse(raw) as Draft : null
    if (draft && (draft.t !== title.value || draft.b !== body.value)) draftOffer.value = draft
  } catch { draftOffer.value = null }
}
function useDraft() {
  const d = draftOffer.value
  if (!d) return
  title.value = d.t; body.value = d.b; Object.assign(fields, d.f)
  if (view.value) setDoc(view.value, d.b)
  draftOffer.value = null
  if (base.value && d.v !== null && d.v !== base.value.version) notice.value = `这份草稿是在第 ${d.v} 版上写的，现在已经是第 ${base.value.version} 版。保存会用草稿覆盖之后的改动（那些版本留在历史里），保存前可以先去看版本历史。`
}
function dropDraft() { try { localStorage.removeItem(draftKey.value) } catch { /* 忽略 */ } draftOffer.value = null }
let draftTimer: ReturnType<typeof setTimeout> | undefined
watch(() => [title.value, body.value, JSON.stringify(fields)], () => {
  if (!dirty.value) return
  clearTimeout(draftTimer)
  draftTimer = setTimeout(() => {
    try {
      localStorage.setItem(draftKey.value, JSON.stringify({ t: title.value, b: body.value, f: fields, v: base.value?.version ?? null, at: new Date().toISOString() } satisfies Draft))
      draftAt.value = when(new Date().toISOString())
    } catch { draftAt.value = '' }
  }, 800)
})

// ---------------------------------------------------------------- 编辑器
const host = ref<HTMLElement | null>(null)
const view = shallowRef<EditorView | null>(null)
const suggestCache = new Map<string, { id: string; title: string; space: string }>()
const spaceTags = useNotes(scope, computed(() => ({ space_id: fields.space_id || undefined })), computed(() => !!fields.space_id))
const knownTags = computed(() => [...new Set((spaceTags.data.value?.pages.flatMap(p => p.items) ?? []).flatMap(n => n.tags))].sort())
async function suggest(q: string): Promise<Suggestion[]> {
  const items = await kb.suggest(q)
  const current = spaceItems.value.find(s => s.id === fields.space_id)
  return items.filter(i => i.id !== noteId.value).map(i => {
    suggestCache.set(normalize(i.title), { id: i.id, title: i.title, space: i.space_name })
    // 同一个知识库或公共库里的直接写标题；别的知识库写成“知识库/标题”，免得同名时连错
    const qualified = i.space_id !== current?.id && i.space_kind !== 'public'
    if (qualified) suggestCache.set(normalize(`${i.space_name}/${i.title}`), { id: i.id, title: i.title, space: i.space_name })
    return { title: i.title, detail: `${SPACE_KIND_LABELS[i.space_kind]} · ${i.space_name}`, insert: qualified ? `${i.space_name}/${i.title}` : i.title }
  })
}
function mountEditor() {
  if (!host.value || view.value) return
  view.value = createEditor(host.value, body.value, { onChange: (doc) => { body.value = doc }, suggest, tags: () => knownTags.value, onSave: () => void save() })
}
onMounted(() => { if (loaded.value) mountEditor() })
onBeforeUnmount(() => { view.value?.destroy(); clearTimeout(draftTimer) })
watch(mode, () => nextTick(mountEditor))

const tools = computed(() => {
  const v = view.value
  if (!v) return []
  return [
    { label: '小节标题', icon: Heading, run: () => linePrefix(v, '## ') },
    { label: '粗体', icon: Bold, run: () => wrap(v, '**'), keys: 'Ctrl/⌘ B' },
    { label: '斜体', icon: Italic, run: () => wrap(v, '*'), keys: 'Ctrl/⌘ I' },
    { label: '删除线', icon: Strikethrough, run: () => wrap(v, '~~') },
    null,
    { label: '无序列表', icon: List, run: () => linePrefix(v, '- ') },
    { label: '有序列表', icon: ListOrdered, run: () => linePrefix(v, i => `${i + 1}. `) },
    { label: '任务列表', icon: ListChecks, run: () => linePrefix(v, '- [ ] ') },
    { label: '引用', icon: Quote, run: () => linePrefix(v, '> ') },
    { label: '代码', icon: Code, run: () => wrap(v, '`', '代码') },
    { label: '表格', icon: Table, run: () => insertBlock(v, '| 列 1 | 列 2 |\n| --- | --- |\n|  |  |') },
    null,
    { label: '链接到笔记', text: '[[ ]]', run: () => insertText(v, '[[]]', 2) },
    { label: '撤销', icon: Undo2, run: () => runUndo(v), keys: 'Ctrl/⌘ Z' },
    { label: '重做', icon: Redo2, run: () => runRedo(v) },
  ]
})

// ---------------------------------------------------------------- 预览、大纲、统计
const sourceLinks = computed(() => new Map((source.data.value?.links ?? []).map(l => [normalize(l.text), l])))
const render = computed(() => createRenderer((target) => {
  const key = normalize(target.text)
  const link = sourceLinks.value.get(key)
  if (link?.note_id && !link.deleted) return { href: `/knowledge/notes/${link.note_id}` }
  const cached = suggestCache.get(key)
  if (cached) return { href: `/knowledge/notes/${cached.id}`, title: `${cached.title}（${cached.space}）` }
  return { href: `/knowledge/notes?q=${encodeURIComponent(target.text)}`, missing: !link }
}))
const preview = computed(() => render.value(body.value))
const outlineItems = computed(() => outline(body.value))
const counts = computed(() => stats(body.value))
function jump(line: number) { if (view.value && mode.value !== 'read') scrollToLine(view.value, line) }

// ---------------------------------------------------------------- 属性
const space = computed(() => spaceItems.value.find(s => s.id === fields.space_id))
const types = computed(() => EDITABLE_TYPES.filter(t => t !== 'brief' || space.value?.kind === 'project' || fields.type === 'brief'))
const tagInput = ref(''), aliasInput = ref('')
function addTag(raw = tagInput.value) {
  const tag = raw.replace(/^#/, '').trim().slice(0, 40)
  if (tag && !fields.tags.includes(tag) && fields.tags.length < 20) fields.tags.push(tag)
  tagInput.value = ''
}
function addAlias() {
  const alias = aliasInput.value.trim().slice(0, 200)
  if (alias && !fields.aliases.includes(alias) && fields.aliases.length < 10) fields.aliases.push(alias)
  aliasInput.value = ''
}
const showProps = ref(false)

// ---------------------------------------------------------------- 保存与合并
const conflict = ref<Note | null>(null)
const merging = ref<MergeChunk[] | null>(null)
function payload() {
  const tags = [...new Set([...fields.tags, ...inlineTags(body.value)])].slice(0, 20)
  return { title: title.value.trim(), body: body.value, type: fields.type, tags, folder: fields.folder.trim(), aliases: fields.aliases, review_at: fields.review_at || null, pinned: fields.pinned, reason: reason.value.trim() }
}
async function save() {
  if (saving.value || !canEdit.value) return
  failure.value = ''
  if (!title.value.trim()) { failure.value = '请先写标题。'; return }
  if (!fields.space_id) { failure.value = '请选择知识库。'; return }
  saving.value = true
  try {
    let saved: Note
    if (isNew.value) {
      saved = await kb.create({ space_id: fields.space_id, ...payload() })
    } else {
      const moved = source.data.value && fields.space_id !== source.data.value.space_id ? { space_id: fields.space_id } : {}
      saved = await kb.update(noteId.value!, { version: base.value!.version, ...moved, ...payload() })
    }
    dropDraft()
    snapshot.value = state()
    await router.push(`/knowledge/notes/${saved.id}`)
  } catch (error) {
    if (error instanceof ApiError && error.code === 'VERSION_CONFLICT' && error.details && typeof error.details === 'object' && 'current' in error.details) {
      conflict.value = (error.details as { current: Note }).current
    } else failure.value = kbError(error)
  } finally { saving.value = false }
}
function startMerge() {
  const theirs = conflict.value
  if (!theirs || !base.value) return
  const chunks = threeWay(base.value.body, body.value, theirs.body)
  if (!conflicts(chunks)) applyMerge(chunks)
  else merging.value = chunks
}
function applyMerge(chunks: MergeChunk[]) {
  const theirs = conflict.value!
  const merged = assemble(chunks)
  body.value = merged
  if (view.value) setDoc(view.value, merged)
  if (base.value && title.value === base.value.title && theirs.title !== base.value.title) title.value = theirs.title
  base.value = { version: theirs.version, title: theirs.title, body: theirs.body }
  merging.value = null; conflict.value = null
  notice.value = `已合并第 ${theirs.version} 版的改动，检查一下再保存。`
}
function keepMine() {
  const theirs = conflict.value
  if (!theirs) return
  base.value = { version: theirs.version, title: theirs.title, body: theirs.body }
  conflict.value = null
  void save()
}
function cancel() { void router.push(isNew.value ? '/knowledge' : `/knowledge/notes/${noteId.value}`) }

onBeforeRouteLeave(() => !dirty.value || window.confirm('有还没保存的修改（草稿已存在这台设备上）。确定离开吗？'))
const unload = (event: BeforeUnloadEvent) => { if (dirty.value) event.preventDefault() }
onMounted(() => window.addEventListener('beforeunload', unload))
onBeforeUnmount(() => window.removeEventListener('beforeunload', unload))
const heading = computed(() => isNew.value ? '新建笔记' : `编辑《${source.data.value?.title ?? ''}》`)
</script>

<template>
  <div class="kb-view has-aside is-mobile-main">
    <main class="kb-main" style="overflow: hidden">
      <KbMobileHeader :title="heading" :back="isNew ? '/knowledge' : `/knowledge/notes/${noteId}`" back-label="取消编辑">
        <button type="button" class="kb-icon-btn" :aria-pressed="showProps" aria-label="属性" @click="showProps = !showProps"><SlidersHorizontal aria-hidden="true" /></button>
        <button type="button" class="kb-btn is-primary is-small" :disabled="saving || !loaded" @click="save">保存</button>
      </KbMobileHeader>
      <h1 class="kb-sr">{{ heading }}</h1>
      <div class="kb-notebar">
        <p class="kb-crumbs">{{ space?.name ?? '' }} / {{ NOTE_TYPE_LABELS[fields.type] }} / <strong>{{ title || '未命名' }}</strong></p>
        <span class="kb-meta kb-desktop-only" role="status">{{ draftAt ? `草稿已存到本机 ${draftAt}` : dirty ? '有未保存的修改' : '' }}</span>
        <div class="kb-segment" role="group" aria-label="编辑方式">
          <button v-for="m in MODES" :key="m.id" type="button" :class="{ 'kb-desktop-only': m.id === 'split' }" :aria-pressed="mode === m.id" @click="mode = m.id">{{ m.label }}</button>
        </div>
        <button type="button" class="kb-btn kb-desktop-only" :disabled="saving" @click="cancel">取消</button>
        <button type="button" class="kb-btn is-primary kb-desktop-only" :disabled="saving || !loaded || !canEdit" @click="save">{{ saving ? '正在保存…' : '保存' }}</button>
      </div>
      <p v-if="!canEdit" class="kb-error" role="alert" style="margin: 12px 16px 0">只有成员及以上可以编辑笔记。</p>
      <p v-if="source.error.value" class="kb-error" role="alert" style="margin: 12px 16px 0">{{ kbError(source.error.value) }}</p>
      <div v-if="conflict" class="kb-notice is-warning" role="alert" style="margin: 12px 16px 0">
        <CircleAlert aria-hidden="true" />
        <span class="kb-grow">你编辑的时候，{{ authorLabel(conflict.updated_by) }} 保存了第 {{ conflict.version }} 版。你的改动还没有覆盖它。</span>
        <button type="button" class="kb-btn is-small" @click="startMerge">合并它的改动</button>
        <button type="button" class="kb-btn is-small" @click="keepMine">用我的覆盖（它的那版留在历史里）</button>
      </div>
      <div v-if="draftOffer" class="kb-notice" role="status" style="margin: 12px 16px 0">
        <span class="kb-grow">这台设备上有一份 {{ when(draftOffer.at) }} 没保存的草稿。</span>
        <button type="button" class="kb-btn is-small" @click="useDraft">恢复草稿</button>
        <button type="button" class="kb-btn is-small" @click="dropDraft">丢弃</button>
      </div>
      <p v-if="notice" class="kb-notice" role="status" style="margin: 12px 16px 0">{{ notice }}</p>
      <p v-if="failure" class="kb-error" role="alert" style="margin: 12px 16px 0">{{ failure }}</p>
      <div v-show="mode !== 'read'" class="kb-format" role="toolbar" aria-label="格式">
        <template v-for="(tool, i) in tools" :key="i">
          <span v-if="!tool" class="kb-format-sep" aria-hidden="true" />
          <button v-else type="button" :aria-label="tool.label" :title="tool.keys ? `${tool.label}（${tool.keys}）` : tool.label" @click="tool.run()">
            <component :is="tool.icon" v-if="tool.icon" aria-hidden="true" /><span v-else aria-hidden="true">{{ tool.text }}</span>
          </button>
        </template>
      </div>
      <div class="kb-editor-wrap">
        <div v-show="mode !== 'read'" class="kb-editor-pane">
          <label class="kb-sr" for="kb-note-title">标题</label>
          <input id="kb-note-title" v-model="title" class="kb-title-input" placeholder="标题" maxlength="200" :disabled="!canEdit">
          <div ref="host" class="kb-cm" />
        </div>
        <div v-if="mode !== 'edit'" class="kb-editor-pane is-preview" aria-label="预览">
          <!-- eslint-disable-next-line vue/no-v-html -- 已经过 DOMPurify 消毒（markdown.ts） -->
          <article class="kb-md"><h1>{{ title || '未命名' }}</h1><div v-html="preview" /></article>
        </div>
      </div>
      <p class="kb-statusbar"><span>Markdown</span><span class="kb-num">{{ counts.words }} 字 · {{ counts.links }} 个链接</span><span class="kb-desktop-only">输入 [[ 链接笔记，行内 #标签 会并入标签</span><span class="kb-desktop-only">保存时生成新版本，可以随时退回</span></p>
    </main>

    <aside class="kb-aside" :class="{ 'is-mobile-sheet': showProps }" aria-label="笔记属性">
      <h2 style="font-size: 15px">属性</h2>
      <label class="kb-field">知识库
        <select v-model="fields.space_id" class="kb-select" :disabled="!canEdit">
          <option v-for="s in spaceItems" :key="s.id" :value="s.id">{{ SPACE_KIND_LABELS[s.kind] }} · {{ s.name }}</option>
        </select>
      </label>
      <label class="kb-field">类型
        <select v-model="fields.type" class="kb-select" :disabled="!canEdit || fields.type === 'correction'">
          <option v-for="t in types" :key="t" :value="t">{{ NOTE_TYPE_LABELS[t] }}</option>
        </select>
      </label>
      <div class="kb-field">
        <span id="kb-tags-label">标签</span>
        <div style="display: flex; flex-wrap: wrap; gap: 6px; align-items: center" role="group" aria-labelledby="kb-tags-label">
          <span v-for="t in fields.tags" :key="t" class="kb-tag">#{{ t }}<button type="button" class="kb-tab-close" style="width: 18px; height: 18px" :aria-label="`去掉标签 ${t}`" @click="fields.tags = fields.tags.filter(x => x !== t)"><X aria-hidden="true" /></button></span>
          <input v-model="tagInput" class="kb-input" style="flex: 1; min-width: 90px" list="kb-known-tags" aria-label="添加标签" placeholder="添加…" @keydown.enter.prevent="addTag()" @keydown.,.prevent="addTag()" @blur="tagInput && addTag()">
          <datalist id="kb-known-tags"><option v-for="t in knownTags" :key="t" :value="t" /></datalist>
        </div>
      </div>
      <div class="kb-field">
        <span id="kb-alias-label">别名（[[别名]] 也能链接到这篇）</span>
        <div style="display: flex; flex-wrap: wrap; gap: 6px; align-items: center" role="group" aria-labelledby="kb-alias-label">
          <span v-for="a in fields.aliases" :key="a" class="kb-chip">{{ a }}<button type="button" class="kb-tab-close" style="width: 18px; height: 18px" :aria-label="`去掉别名 ${a}`" @click="fields.aliases = fields.aliases.filter(x => x !== a)"><X aria-hidden="true" /></button></span>
          <input v-model="aliasInput" class="kb-input" style="flex: 1; min-width: 90px" aria-label="添加别名" placeholder="添加…" @keydown.enter.prevent="addAlias" @blur="aliasInput && addAlias()">
        </div>
      </div>
      <label class="kb-field">文件夹（可选）<input v-model="fields.folder" class="kb-input" maxlength="200" placeholder="例如 流程/发布"></label>
      <label class="kb-field">复核日期<input v-model="fields.review_at" class="kb-input" type="date"></label>
      <label class="kb-check"><input v-model="fields.pinned" type="checkbox">置顶（目录里排在前面）</label>
      <label class="kb-field">这次修改的原因（可选，写进变更记录）<input v-model="reason" class="kb-input" maxlength="500"></label>
      <div v-if="outlineItems.length" class="kb-side-section kb-divider" style="padding-top: 12px">
        <h2>大纲</h2>
        <button v-for="o in outlineItems" :key="o.line" type="button" class="kb-list-item" style="min-height: 30px" :style="{ paddingLeft: `${(o.level - 1) * 12 + 10}px` }" @click="jump(o.line)">{{ o.text }}</button>
      </div>
      <p v-if="space && space.kind !== 'agent'" class="kb-meta kb-divider" style="margin: 0; padding-top: 12px">
        {{ space.kind === 'project' ? (space.agents.filter(a => a.access === 'read_write').map(a => a.name).join('、') || '还没有智能体') + ' 也能直接改这篇笔记。' : '公共知识库对智能体的权限：' + (space.agent_access === 'read_write' ? '可以读写' : space.agent_access === 'read' ? '只读' : '不能访问') + '。' }}
        每次保存都是一个新版本，在“变更记录”里可以撤销。
      </p>
      <RouterLink v-if="!isNew" :to="`/knowledge/notes/${noteId}`" class="kb-meta">查看版本历史</RouterLink>
    </aside>

    <ModalPanel v-if="merging && conflict" title="合并修改" @close="merging = null">
      <div class="kb-merge">
        <p style="margin: 0">你和 {{ authorLabel(conflict.updated_by) }} 改了同一处，共 {{ conflicts(merging) }} 段。每一段选一个：</p>
        <template v-for="(c, i) in merging" :key="i">
          <div v-if="c.kind === 'conflict'" class="kb-merge-conflict">
            <div class="is-mine"><strong>我的：</strong>{{ c.mine.join('\n') || '（删掉了）' }}</div>
            <div class="is-theirs"><strong>{{ conflict.updated_by.name }} 的（第 {{ conflict.version }} 版）：</strong>{{ c.theirs.join('\n') || '（删掉了）' }}</div>
            <div class="kb-merge-pick" role="group" :aria-label="`第 ${i + 1} 段用哪个`">
              <button type="button" class="kb-btn is-small" :aria-pressed="c.pick === 'mine'" @click="c.pick = 'mine'">用我的</button>
              <button type="button" class="kb-btn is-small" :aria-pressed="c.pick === 'theirs'" @click="c.pick = 'theirs'">用它的</button>
              <button type="button" class="kb-btn is-small" :aria-pressed="c.pick === 'both'" @click="c.pick = 'both'">都保留</button>
            </div>
          </div>
        </template>
        <div style="display: flex; justify-content: flex-end; gap: 8px">
          <button type="button" class="kb-btn" @click="merging = null">取消</button>
          <button type="button" class="kb-btn is-primary" :disabled="unresolved(merging)" @click="applyMerge(merging)">合并到编辑器</button>
        </div>
      </div>
    </ModalPanel>
  </div>
</template>
