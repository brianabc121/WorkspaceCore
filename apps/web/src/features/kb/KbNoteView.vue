<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { ArchiveRestore, CircleAlert, CircleCheck, Ellipsis, History, Pin, SquarePen, Trash2 } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { ApiError, errorMessage } from '../../services/api'
import ModalPanel from '../../components/ModalPanel.vue'
import { CHANGE_LABELS, NOTE_TYPE_LABELS, SPACE_KIND_LABELS, authorLabel, initial, useKb, useLocalGraph, useNote, useNotes, useRevision, useRevisions, useSpaces, type Note, type NoteSummary } from './api'
import { useMediaQuery } from '../../composables/useMediaQuery'
import { cleanSnippet, dateLabel, nodeHref, noteHtml, when } from './format'
import { useOpenTabs } from './tabs'
import KbForceGraph from './KbForceGraph.vue'
import KbTabs from './KbTabs.vue'
import KbMobileHeader from './KbMobileHeader.vue'
import { spaceLink } from './KbSpaceList.vue'

// 笔记（T-0099，效果图“电脑端 · 笔记”“移动端 · 笔记”）：左栏本知识库的目录，中间属性与正文，
// 右栏局部图谱、反向链接、被智能体使用的情况、版本历史。正文经 markdown.ts 渲染并消毒。
const auth = useAuthStore(), route = useRoute(), router = useRouter(), scope = auth.workspaceId!
const canEdit = computed(() => ['owner', 'admin', 'member'].includes(auth.currentWorkspace?.role ?? ''))
const id = computed(() => typeof route.params.id === 'string' ? route.params.id : null)
const query = useNote(scope, id)
const note = computed(() => query.data.value)
const spaces = useSpaces(scope)
const space = computed(() => spaces.data.value?.items.find(s => s.id === note.value?.space_id))
const kb = useKb(scope)
const tabs = useOpenTabs()
watch(note, (n) => { if (n && !n.deleted_at) tabs.open({ id: n.id, title: n.title }) })

// 目录：同一个知识库里的笔记，按类型分组（项目简报和置顶的在最前）
const dirFilters = computed(() => ({ space_id: note.value?.space_id }))
const dir = useNotes(scope, dirFilters, computed(() => !!note.value?.space_id))
const dirGroups = computed(() => {
  const items = dir.data.value?.pages.flatMap(p => p.items) ?? []
  const top = items.filter(n => n.type === 'brief' || n.pinned)
  const rest = items.filter(n => n.type !== 'brief' && !n.pinned)
  const byType = new Map<string, NoteSummary[]>()
  for (const n of rest) byType.set(n.type, [...(byType.get(n.type) ?? []), n])
  return [{ label: '', items: top }, ...[...byType.entries()].map(([type, list]) => ({ label: `${NOTE_TYPE_LABELS[type as Note['type']]} · ${list.length}`, items: list }))].filter(g => g.items.length)
})

// 正文：[[链接]] 按后端解析的结果找到目标（找不到的点了去新建）
const html = computed(() => note.value ? noteHtml(note.value) : '')
function follow(event: MouseEvent) {
  const a = (event.target as HTMLElement).closest('a')
  const href = a?.getAttribute('href')
  if (!a || !href || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || a.target === '_blank') return
  if (href.startsWith('#')) return
  if (href.startsWith('/')) { event.preventDefault(); void router.push(href) }
}

// 右栏
const panel = ref<'links' | 'graph' | 'versions' | null>(null)
const wide = useMediaQuery('(min-width: 761px)')
const depth = ref(1)
const local = useLocalGraph(scope, id, depth)
const revisions = useRevisions(scope, id, computed(() => wide.value || panel.value === 'versions'))
const viewing = ref<number | null>(null)
const revision = useRevision(scope, id, viewing)
const busy = ref(false), message = ref(''), failure = ref('')
function flash(text: string) { message.value = text; setTimeout(() => { if (message.value === text) message.value = '' }, 4000) }
async function act(work: () => Promise<unknown>, done: string) {
  if (busy.value) return
  busy.value = true; failure.value = ''
  try { await work(); flash(done) }
  catch (error) {
    failure.value = error instanceof ApiError && error.code === 'VERSION_CONFLICT' ? '这篇笔记刚被修改过，已刷新为最新内容，请再试一次。' : errorMessage(error)
    if (error instanceof ApiError && error.code === 'VERSION_CONFLICT') await query.refetch()
  } finally { busy.value = false }
}
const confirmToggle = () => note.value && act(() => kb.confirm(note.value!.id, note.value!.version, !note.value!.confirmed_at), note.value.confirmed_at ? '已取消“人已确认”' : '已标为人已确认')
const deleting = ref(false), deleteReason = ref('')
async function remove() {
  const n = note.value
  if (!n) return
  await act(() => kb.remove(n.id, n.version, deleteReason.value.trim()), '已放进回收站，30 天内可以恢复')
  if (!failure.value) { deleting.value = false; tabs.close(n.id); void router.push(space.value ? spaceLink(space.value) : '/knowledge') }
}
const restore = () => note.value && act(() => kb.restore(note.value!.id), '已从回收站恢复')
const revertReason = ref('')
async function revertTo(version: number) {
  const n = note.value
  if (!n) return
  await act(() => kb.revert(n.id, n.version, version, revertReason.value.trim()), `已退回到第 ${version} 版的内容（生成第 ${n.version + 1} 版）`)
  if (!failure.value) { viewing.value = null; revertReason.value = '' }
}
const more = ref(false)
async function copyLink() { more.value = false; try { await navigator.clipboard.writeText(`[[${space.value && space.value.kind !== 'public' ? space.value.name + '/' : ''}${note.value?.title}]]`); flash('已复制 [[链接]]，粘贴到别的笔记里') } catch { failure.value = '复制失败，请手动输入 [[标题]]。' } }
const usage = computed(() => note.value?.usage)
const evidenceHref = (e: Note['evidence'][number]) => e.kind === 'note' ? `/knowledge/notes/${e.id}` : e.kind === 'task' ? `/tasks?task=${encodeURIComponent(e.id)}` : e.kind === 'project' ? `/tasks?project_id=${encodeURIComponent(e.id)}` : null
const evidenceLabel: Record<Note['evidence'][number]['kind'], string> = { task: '任务', project: '项目', note: '笔记', conversation: '会话', message: '消息', bookmark: '收藏文档' }
const snippetParts = (raw: string, title: string) => {
  const snippet = cleanSnippet(raw), i = snippet.indexOf(title)
  return i < 0 ? [snippet, '', ''] : [snippet.slice(0, i), title, snippet.slice(i + title.length)]
}
function openLocal(sel: string) {
  const target = local.data.value?.nodes.find(x => x.id === sel)
  if (target && sel !== note.value?.id) void router.push(nodeHref(target))
}
const notFound = computed(() => query.error.value instanceof ApiError && query.error.value.status === 404)
</script>

<template>
  <div class="kb-view has-aside is-mobile-main">
    <!-- 左栏：目录 -->
    <aside class="kb-side" aria-label="知识库目录">
      <RouterLink v-if="space" :to="spaceLink(space)" class="kb-list-item" style="min-height: 48px">
        <span class="kb-dot" :class="`k-${space.kind}`" aria-hidden="true" />
        <span class="kb-stack kb-grow"><span class="kb-meta">{{ SPACE_KIND_LABELS[space.kind] }}知识库</span><strong>{{ space.name }}</strong></span>
      </RouterLink>
      <ul class="kb-list">
        <template v-for="g in dirGroups" :key="g.label">
          <li v-if="g.label"><p class="kb-tree-folder">{{ g.label }}</p></li>
          <li v-for="n in g.items" :key="n.id">
            <RouterLink :to="`/knowledge/notes/${n.id}`" class="kb-list-item" :aria-current="n.id === id ? 'page' : undefined">
              <span class="kb-grow">{{ n.title }}</span>
              <span v-if="n.type === 'brief'" class="kb-meta">简报</span><Pin v-else-if="n.pinned" aria-label="置顶" style="width: 13px; height: 13px; color: var(--kb-muted)" />
            </RouterLink>
          </li>
        </template>
      </ul>
      <p v-if="space?.kind === 'project'" class="kb-meta kb-divider" style="margin: 0; padding-top: 12px">本项目的智能体：{{ space.agents.length ? space.agents.map(a => a.name).join('、') : '还没有' }}<br>它们可以直接读写；每次修改都留版本，可以撤销</p>
    </aside>

    <main class="kb-main">
      <KbMobileHeader :title="note?.title ?? '笔记'" :back="space ? spaceLink(space) : '/knowledge'">
        <RouterLink v-if="canEdit && note && !note.deleted_at && note.type !== 'correction'" :to="`/knowledge/notes/${note.id}/edit`" class="kb-icon-btn" aria-label="编辑"><SquarePen aria-hidden="true" /></RouterLink>
      </KbMobileHeader>
      <KbTabs />
      <div v-if="note" class="kb-notebar">
        <p class="kb-crumbs">{{ space?.name ?? note.space_name }}<template v-if="note.folder"> / {{ note.folder }}</template> / {{ NOTE_TYPE_LABELS[note.type] }} / <strong>{{ note.title }}</strong></p>
        <RouterLink v-if="canEdit && !note.deleted_at && note.type !== 'correction'" :to="`/knowledge/notes/${note.id}/edit`" class="kb-btn is-primary kb-desktop-only"><SquarePen aria-hidden="true" />编辑</RouterLink>
        <button type="button" class="kb-btn kb-desktop-only" @click="viewing = note.version"><History aria-hidden="true" />历史版本</button>
        <div style="position: relative">
          <button type="button" class="kb-icon-btn" aria-label="更多操作" :aria-expanded="more" @click="more = !more"><Ellipsis aria-hidden="true" /></button>
          <div v-if="more" class="kb-card" role="menu" style="position: absolute; right: 0; top: 40px; z-index: 20; min-width: 200px; padding: 6px; gap: 2px; box-shadow: var(--kb-shadow)" @keydown.esc="more = false">
            <button type="button" role="menuitem" class="kb-list-item" @click="copyLink">复制 [[链接]]</button>
            <button v-if="canEdit && !note.deleted_at" type="button" role="menuitem" class="kb-list-item" :disabled="busy" @click="more = false; confirmToggle()">{{ note.confirmed_at ? '取消“人已确认”' : '标为人已确认' }}</button>
            <RouterLink :to="{ path: '/knowledge/graph' }" role="menuitem" class="kb-list-item" @click="more = false">在图谱中查看</RouterLink>
            <button v-if="canEdit && !note.deleted_at && note.type !== 'correction'" type="button" role="menuitem" class="kb-list-item" style="color: var(--kb-danger)" @click="more = false; deleting = true"><Trash2 aria-hidden="true" style="width: 16px; height: 16px" />删除（放进回收站）</button>
          </div>
        </div>
      </div>
      <p v-if="query.isPending.value" class="kb-empty" role="status">正在加载笔记…</p>
      <div v-else-if="notFound" class="kb-reading"><p class="kb-empty">找不到这篇笔记，可能已经被彻底删除。<RouterLink to="/knowledge">回到知识库</RouterLink></p></div>
      <p v-else-if="query.error.value" class="kb-error" role="alert" style="margin: 16px">{{ errorMessage(query.error.value) }}</p>
      <div v-else-if="note" class="kb-reading">
        <p v-if="failure" class="kb-error" role="alert">{{ failure }}</p>
        <div v-if="note.deleted_at" class="kb-notice is-warning" role="status">
          <Trash2 aria-hidden="true" /><span class="kb-grow">这篇笔记在回收站里，{{ dateLabel(note.purge_after) }}以后会被彻底删除。恢复后智能体才能再读到它。</span>
          <button v-if="canEdit" type="button" class="kb-btn is-small" :disabled="busy" @click="restore"><ArchiveRestore aria-hidden="true" />恢复</button>
        </div>
        <div v-if="note.conflict_note" class="kb-notice is-warning" role="note"><CircleAlert aria-hidden="true" /><span class="kb-grow">{{ note.conflict_note }}。核对后编辑保存一次，提醒就会去掉。</span></div>
        <div v-if="note.type === 'brief'" class="kb-notice" role="note"><span class="kb-grow">这是项目简报：参与本项目的智能体接到任务时先读它。只有人能修改。</span></div>
        <details class="kb-mobile-only kb-card" style="padding: 10px 12px; flex-direction: column">
          <summary class="kb-meta" style="cursor: pointer">{{ authorLabel(note.created_by) }} 写入{{ note.confirmed_by ? ` · ${note.confirmed_by.name} 已确认` : '' }} · 第 {{ note.version }} 版</summary>
        </details>
        <dl class="kb-props kb-desktop-only" aria-label="笔记属性">
          <dt>类型</dt><dd>{{ NOTE_TYPE_LABELS[note.type] }}</dd>
          <template v-if="note.tags.length"><dt>标签</dt><dd><RouterLink v-for="t in note.tags" :key="t" class="kb-tag" :to="{ path: '/knowledge/notes', query: { tag: t } }">#{{ t }}</RouterLink></dd></template>
          <template v-if="note.aliases.length"><dt>别名</dt><dd>{{ note.aliases.join('、') }}</dd></template>
          <dt>作者</dt>
          <dd><span class="kb-avatar" :class="{ 'is-user': note.created_by.kind !== 'agent' }" style="width: 22px; height: 22px; font-size: 11px" aria-hidden="true">{{ initial(note.created_by.name) }}</span>{{ authorLabel(note.created_by) }} 写入<template v-if="note.updated_by.name !== note.created_by.name || note.version > 1"> · {{ authorLabel(note.updated_by) }} {{ when(note.updated_at) }}修改</template></dd>
          <dt>状态</dt>
          <dd>
            <span v-if="note.confirmed_by" class="kb-badge is-good"><CircleCheck aria-hidden="true" />{{ note.confirmed_by.name }} {{ when(note.confirmed_at) }}确认过</span>
            <span v-else-if="note.updated_by.kind === 'agent'" class="kb-badge">智能体写入，人还没确认</span>
            <span v-else class="kb-badge">人写的</span>
            <button v-if="canEdit && !note.deleted_at" type="button" class="kb-btn is-small" :disabled="busy" @click="confirmToggle">{{ note.confirmed_at ? '取消确认' : '标为人已确认' }}</button>
          </dd>
          <template v-if="note.reason"><dt>写入原因</dt><dd>“{{ note.reason }}”</dd></template>
          <template v-if="note.evidence.length"><dt>依据</dt><dd><template v-for="e in note.evidence" :key="e.kind + e.id"><RouterLink v-if="evidenceHref(e)" :to="evidenceHref(e)!" class="kb-tag">{{ evidenceLabel[e.kind] }}</RouterLink><span v-else class="kb-badge">{{ evidenceLabel[e.kind] }} {{ e.id.slice(0, 8) }}</span></template></dd></template>
          <template v-if="note.review_at"><dt>复核日期</dt><dd>{{ dateLabel(note.review_at) }}（到期后提醒重新确认）</dd></template>
          <dt>版本</dt><dd class="kb-num">第 {{ note.version }} 版</dd>
        </dl>
        <!-- eslint-disable-next-line vue/no-v-html -- 已经过 DOMPurify 消毒（markdown.ts） -->
        <article class="kb-md" @click="follow" v-html="html" />
        <p v-if="!note.body.trim()" class="kb-meta">（还没有正文）</p>
      </div>
      <nav v-if="note" class="kb-mobile-only" aria-label="笔记关系" style="gap: 8px; padding: 10px 16px 18px; flex-wrap: wrap">
        <button type="button" class="kb-btn" :aria-pressed="panel === 'links'" @click="panel = panel === 'links' ? null : 'links'">反向链接 {{ note.backlinks.length }}</button>
        <button type="button" class="kb-btn" :aria-pressed="panel === 'graph'" @click="panel = panel === 'graph' ? null : 'graph'">局部图谱</button>
        <button type="button" class="kb-btn" :aria-pressed="panel === 'versions'" @click="panel = panel === 'versions' ? null : 'versions'">版本 {{ note.version }}</button>
      </nav>
    </main>

    <!-- 右栏：链接、使用情况、版本 -->
    <aside v-if="note" class="kb-aside" :class="{ 'is-mobile-sheet': !!panel }" aria-label="笔记关系">
      <section v-show="wide || panel === 'graph'" class="kb-side-section" aria-label="局部图谱">
        <div class="kb-side-head">
          <h2>局部图谱</h2>
          <div class="kb-segment" role="group" aria-label="显示几层">
            <button type="button" :aria-pressed="depth === 1" @click="depth = 1">1 层</button>
            <button type="button" :aria-pressed="depth === 2" @click="depth = 2">2 层</button>
          </div>
        </div>
        <div class="kb-local-graph">
          <KbForceGraph :graph="local.data.value" :center="note.id" :selected="note.id" compact :label="`${note.title}和相连的笔记`" @open="n => router.push(nodeHref(n))" @select="openLocal" />
        </div>
        <p class="kb-meta" style="display: flex; gap: 12px; margin: 0"><span><span class="kb-dot is-small k-project" aria-hidden="true" /> 项目</span><span><span class="kb-dot is-small k-agent" aria-hidden="true" /> 智能体</span><span><span class="kb-dot is-small k-public" aria-hidden="true" /> 公共</span></p>
      </section>
      <section v-show="wide || panel === 'links'" class="kb-side-section" aria-label="反向链接">
        <h2>反向链接 · {{ note.backlinks.length }}</h2>
        <RouterLink v-for="b in note.backlinks" :key="b.note_id" :to="`/knowledge/notes/${b.note_id}`" class="kb-backlink">
          <span style="display: flex; align-items: center; gap: 6px"><span class="kb-dot is-small" :class="`k-${b.space_kind}`" aria-hidden="true" />{{ b.title }}<span class="kb-meta" style="margin-left: auto">{{ b.space_name }}</span></span>
          <span v-if="b.snippet" class="kb-snippet">…{{ snippetParts(b.snippet, note.title)[0] }}<mark>{{ snippetParts(b.snippet, note.title)[1] }}</mark>{{ snippetParts(b.snippet, note.title)[2] }}…</span>
        </RouterLink>
        <p v-if="!note.backlinks.length" class="kb-meta" style="margin: 0">还没有别的笔记链接到这里。在别的笔记里写 [[{{ note.title }}]] 就会出现在这里。</p>
      </section>
      <section v-show="wide" class="kb-side-section" aria-label="被智能体使用">
        <h2>被智能体使用</h2>
        <p v-if="usage && usage.reads_30d" class="kb-meta" style="margin: 0; line-height: 1.6">近 30 天被读取 <strong class="kb-num" style="color: var(--kb-text)">{{ usage.reads_30d }}</strong> 次：{{ usage.by_agent.map(a => `${a.name} ${a.reads} 次`).join('、') }}。最近一次是 {{ dateLabel(usage.last_read_at) }}。</p>
        <p v-else class="kb-meta" style="margin: 0">近 30 天还没有智能体读过。</p>
      </section>
      <section v-show="wide || panel === 'versions'" class="kb-side-section" aria-label="版本历史">
        <h2>版本历史</h2>
        <p v-if="revisions.isPending.value" class="kb-meta" role="status">正在加载…</p>
        <div v-for="r in revisions.data.value?.items.slice(0, 8) ?? []" :key="r.version" class="kb-version">
          <span class="kb-avatar" :class="{ 'is-user': r.author.kind !== 'agent' }" style="width: 26px; height: 26px; font-size: 11px" aria-hidden="true">{{ initial(r.author.name) }}</span>
          <span class="kb-stack" style="flex: 1">
            <span>第 {{ r.version }} 版 · {{ CHANGE_LABELS[r.change] }}<span class="kb-meta kb-num"> +{{ r.added }} −{{ r.removed }}</span></span>
            <span class="kb-meta">{{ authorLabel(r.author) }} · {{ when(r.created_at) }}</span>
            <span v-if="r.reason" class="kb-meta">“{{ r.reason }}”</span>
          </span>
          <button type="button" class="kb-btn is-small" :aria-label="`查看第 ${r.version} 版`" @click="viewing = r.version">查看</button>
        </div>
      </section>
    </aside>

    <ModalPanel v-if="viewing && note" :title="`第 ${viewing} 版`" :busy="busy" @close="viewing = null">
      <p v-if="revision.isPending.value" role="status">正在加载…</p>
      <template v-else-if="revision.data.value">
        <p class="kb-meta" style="margin: 0 0 10px">{{ CHANGE_LABELS[revision.data.value.change] }} · {{ authorLabel(revision.data.value.author) }} · {{ when(revision.data.value.created_at) }}<template v-if="revision.data.value.reason"> · “{{ revision.data.value.reason }}”</template></p>
        <div class="kb-diff" aria-label="和上一版相比">
          <div v-for="(d, i) in revision.data.value.diff" :key="i" :class="{ 'is-add': d.sign === '+', 'is-del': d.sign === '-' }"><span class="kb-sign" aria-hidden="true">{{ d.sign }}</span><span><span class="kb-sr">{{ d.sign === '+' ? '新增：' : d.sign === '-' ? '删除：' : '' }}</span>{{ d.text || ' ' }}</span></div>
          <div v-if="!revision.data.value.diff.length"><span>和上一版相比正文没有变化</span></div>
        </div>
        <div v-if="canEdit && viewing !== note.version && !note.deleted_at" style="display: flex; flex-direction: column; gap: 8px; margin-top: 14px">
          <label class="kb-field">退回的原因（会写进变更记录；退回智能体的改动时，原因会告诉它）<input v-model="revertReason" class="kb-input" maxlength="500"></label>
          <button type="button" class="kb-btn is-primary" :disabled="busy" @click="revertTo(viewing)">退回到这一版的内容</button>
        </div>
        <p v-if="failure" class="kb-error" role="alert" style="margin-top: 10px">{{ failure }}</p>
      </template>
      <p v-else-if="revision.error.value" class="kb-error" role="alert">{{ errorMessage(revision.error.value) }}</p>
    </ModalPanel>
    <ModalPanel v-if="deleting && note" title="删除笔记" :busy="busy" @close="deleting = false">
      <p>《{{ note.title }}》会放进回收站，30 天后彻底删除；这期间可以恢复。智能体读不到回收站里的笔记。</p>
      <label class="kb-field">原因（可选，会写进变更记录）<input v-model="deleteReason" class="kb-input" maxlength="500"></label>
      <p v-if="failure" class="kb-error" role="alert" style="margin-top: 10px">{{ failure }}</p>
      <div style="display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px">
        <button type="button" class="kb-btn" :disabled="busy" @click="deleting = false">取消</button>
        <button type="button" class="kb-btn is-primary" style="background: var(--kb-danger); border-color: var(--kb-danger)" :disabled="busy" @click="remove">放进回收站</button>
      </div>
    </ModalPanel>
    <p v-if="message" class="kb-toast" role="status">{{ message }}</p>
  </div>
</template>
