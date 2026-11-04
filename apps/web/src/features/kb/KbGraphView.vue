<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { CircleAlert, CircleCheck, Info, List, Plus, Search, SquarePen, Waypoints } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { errorMessage } from '../../services/api'
import { CHANGE_LABELS, NOTE_TYPE_LABELS, SPACE_KIND_LABELS, authorLabel, useChanges, useGraph, useNote, useSpaces, type ColorBy, type GraphNode } from './api'
import { excerpt, isToday, nodeHref, statusOf, when } from './format'
import KbForceGraph from './KbForceGraph.vue'
import KbSpaceList from './KbSpaceList.vue'
import KbTabs from './KbTabs.vue'
import KbMobileHeader from './KbMobileHeader.vue'

// 知识图谱（T-0099，效果图“电脑端 · 知识图谱”）：左栏知识库分组，中间图谱，右栏所选笔记。
// 手机上 /knowledge 是知识库列表，/knowledge/graph 是图谱（效果图“移动端”）。
const auth = useAuthStore(), route = useRoute(), router = useRouter(), scope = auth.workspaceId!
const canEdit = computed(() => ['owner', 'admin', 'member'].includes(auth.currentWorkspace?.role ?? ''))
const graphOnly = computed(() => !!route.meta.kbGraphOnly)

const spaces = useSpaces(scope)
const spaceItems = computed(() => spaces.data.value?.items ?? [])
const spaceById = computed(() => new Map(spaceItems.value.map(s => [s.id, s])))
const resources = ref(true)
const graph = useGraph(scope, computed(() => ({ spaceIds: [], resources: resources.value })))
const changes = useChanges(scope, ref({}))
const recent = computed(() => (changes.data.value?.pages[0]?.items ?? []).filter(c => c.kind === 'revision' && c.note_id && !c.note_deleted && c.author.kind !== 'system').slice(0, 4))
const today = computed(() => {
  const items = changes.data.value?.pages[0]?.items ?? []
  const todays = items.filter(c => isToday(c.created_at))
  return {
    edits: todays.filter(c => c.kind === 'revision' && c.author.kind === 'agent').length,
    deletes: todays.filter(c => c.change === 'delete').length,
    blocked: todays.filter(c => c.kind === 'guard').length,
    more: todays.length === items.length && !!changes.hasNextPage.value,
  }
})

const query = ref(''), colorBy = ref<ColorBy>('space'), orphans = ref(true), allLabels = ref(false), listView = ref(false)
const hidden = ref<string[]>([]), focus = ref<string | null>(typeof route.query.focus === 'string' ? route.query.focus : null), selected = ref<string | null>(null)
const nodes = computed(() => graph.data.value?.nodes ?? [])
const nodeById = computed(() => new Map(nodes.value.map(n => [n.id, n])))
watch(nodes, (list) => {
  if (selected.value && nodeById.value.has(selected.value)) return
  const notes = list.filter(n => n.kind === 'note')
  selected.value = notes.reduce<GraphNode | null>((best, n) => (!best || n.degree > best.degree ? n : best), null)?.id ?? null
}, { immediate: true })
const kindOf = (n: GraphNode) => n.kind === 'note' ? (n.space_kind ?? 'public') : 'resource'
const legend = computed(() => (['project', 'agent', 'public', 'resource'] as const).map(k => ({
  key: k, label: k === 'resource' ? '任务与文档' : SPACE_KIND_LABELS[k], count: nodes.value.filter(n => kindOf(n) === k).length,
})).filter(k => k.count || k.key !== 'resource'))
function toggleKind(key: string) { hidden.value = hidden.value.includes(key) ? hidden.value.filter(k => k !== key) : [...hidden.value, key] }
const edges = computed(() => graph.data.value?.edges ?? [])
const degreeShown = computed(() => {
  const visible = new Set(nodes.value.filter(n => !hidden.value.includes(kindOf(n))).map(n => n.id))
  const count = edges.value.filter(e => visible.has(e.source) && visible.has(e.target)).length
  const linked = new Set(edges.value.flatMap(e => [e.source, e.target]))
  const orphanCount = nodes.value.filter(n => n.kind === 'note' && !linked.has(n.id)).length
  return { notes: [...visible].filter(id => nodeById.value.get(id)?.kind === 'note').length, edges: count, orphans: orphanCount }
})
const listRows = computed(() => [...nodes.value].filter(n => !hidden.value.includes(kindOf(n)) && (!query.value || n.title.toLowerCase().includes(query.value.toLowerCase()))).sort((a, b) => b.degree - a.degree))

const graphRef = ref<InstanceType<typeof KbForceGraph> | null>(null)
const selectedNode = computed(() => (selected.value ? nodeById.value.get(selected.value) : undefined) ?? null)
const noteId = computed(() => selectedNode.value?.kind === 'note' ? selectedNode.value.id : null)
const note = useNote(scope, noteId)
const neighbors = computed(() => {
  const id = selected.value
  if (!id) return []
  const ids = new Set(edges.value.flatMap(e => e.source === id ? [e.target] : e.target === id ? [e.source] : []))
  return [...ids].map(i => nodeById.value.get(i)).filter((n): n is GraphNode => !!n).sort((a, b) => b.degree - a.degree).slice(0, 8)
})
function spaceName(n: GraphNode) { return n.space_id ? spaceById.value.get(n.space_id)?.name ?? '' : n.kind === 'task' ? '任务' : n.kind === 'project' ? '项目' : '收藏文档' }
function open(n: GraphNode) { void router.push(nodeHref(n)) }
function searchNotes() { if (query.value.trim()) void router.push({ path: '/knowledge/notes', query: { q: query.value.trim() } }) }
const sideQuery = ref('')
function sideSearch() { if (sideQuery.value.trim()) void router.push({ path: '/knowledge/notes', query: { q: sideQuery.value.trim() } }) }
const error = computed(() => graph.error.value ?? spaces.error.value)
</script>

<template>
  <div class="kb-view has-aside" :class="graphOnly ? 'is-mobile-main' : 'is-mobile-list'">
    <!-- 左栏：知识库分组（手机首页） -->
    <aside class="kb-side" aria-label="知识库分组">
      <KbMobileHeader title="知识库" back="/">
        <RouterLink to="/knowledge/notes" class="kb-icon-btn" aria-label="搜索笔记"><Search aria-hidden="true" /></RouterLink>
        <RouterLink v-if="canEdit" to="/knowledge/notes/new" class="kb-icon-btn" aria-label="新建笔记"><SquarePen aria-hidden="true" /></RouterLink>
      </KbMobileHeader>
      <div class="kb-head kb-desktop-only">
        <h1 class="kb-title">知识库</h1>
        <RouterLink v-if="canEdit" to="/knowledge/notes/new" class="kb-icon-btn" aria-label="新建笔记" title="新建笔记"><SquarePen aria-hidden="true" /></RouterLink>
      </div>
      <form class="kb-search kb-desktop-only" role="search" @submit.prevent="sideSearch">
        <Search aria-hidden="true" />
        <label class="kb-sr" for="kb-side-search">搜索笔记</label>
        <input id="kb-side-search" v-model="sideQuery" type="search" placeholder="搜索笔记、标签">
      </form>
      <RouterLink to="/knowledge/changes" class="kb-card kb-mobile-only" style="padding: 12px 14px; text-decoration: none; color: var(--kb-text)">
        <span class="kb-stack">
          <strong>今天智能体改动 {{ today.edits }}{{ today.more ? '+' : '' }} 次</strong>
          <span class="kb-meta">删除 {{ today.deletes }} 篇（在回收站）· 拦下 {{ today.blocked }} 次 · 查看变更记录</span>
        </span>
      </RouterLink>
      <p v-if="spaces.isPending.value" class="kb-meta" role="status">正在加载知识库…</p>
      <p v-else-if="spaces.error.value" class="kb-error" role="alert">{{ errorMessage(spaces.error.value) }}</p>
      <KbSpaceList v-else class="kb-desktop-only" :spaces="spaceItems" :focus="focus" :total="graph.data.value?.total_notes" @focus="focus = $event" />
      <KbSpaceList v-if="spaceItems.length" class="kb-mobile-only" :spaces="spaceItems" links />
      <div v-if="recent.length" class="kb-divider" style="padding-top: 12px; display: flex; flex-direction: column; gap: 2px">
        <p class="kb-group-label" style="margin-top: 0">最近写入</p>
        <RouterLink v-for="c in recent" :key="c.id" :to="`/knowledge/notes/${c.note_id}`" class="kb-recent">
          <span>{{ c.note_title }}</span>
          <span class="kb-meta">{{ c.author.name }} · {{ when(c.created_at) }} · {{ c.change ? CHANGE_LABELS[c.change] : '' }}</span>
        </RouterLink>
      </div>
      <RouterLink to="/knowledge/docs" class="kb-recent kb-mobile-only"><span>外部文档</span><span class="kb-meta">AFFiNE 文档收藏</span></RouterLink>
    </aside>

    <!-- 中间：图谱 -->
    <main class="kb-main" style="overflow: hidden">
      <KbMobileHeader title="知识图谱" back="/knowledge" back-label="返回知识库">
        <button type="button" class="kb-icon-btn" :aria-pressed="listView" aria-label="列表视图" @click="listView = !listView"><List aria-hidden="true" /></button>
      </KbMobileHeader>
      <KbTabs />
      <div class="kb-toolbar">
        <form class="kb-search" role="search" @submit.prevent="searchNotes">
          <Search aria-hidden="true" />
          <label class="kb-sr" for="kb-graph-search">在图谱中查找</label>
          <input id="kb-graph-search" v-model="query" type="search" placeholder="在图谱中查找">
        </form>
        <label class="kb-check" style="color: var(--kb-muted)">着色
          <select v-model="colorBy" class="kb-select">
            <option value="space">按知识库类型</option><option value="author">按作者（人 / 智能体）</option><option value="confirmed">按是否人已确认</option>
          </select>
        </label>
        <label class="kb-check"><input v-model="orphans" type="checkbox">孤立笔记</label>
        <label class="kb-check"><input v-model="allLabels" type="checkbox">显示全部标题</label>
        <label class="kb-check"><input v-model="resources" type="checkbox">任务与文档</label>
        <span style="flex: 1" />
        <button type="button" class="kb-btn is-small kb-desktop-only" :aria-pressed="listView" @click="listView = !listView"><List aria-hidden="true" />{{ listView ? '图谱视图' : '列表视图' }}</button>
        <div v-if="!listView" class="kb-zoom" role="group" aria-label="缩放">
          <button type="button" aria-label="缩小" @click="graphRef?.zoomOut()">−</button>
          <button type="button" @click="graphRef?.fit()">适应窗口</button>
          <button type="button" aria-label="放大" @click="graphRef?.zoomIn()">+</button>
        </div>
      </div>
      <div class="kb-graph-wrap">
        <p v-if="error" class="kb-error" role="alert">{{ errorMessage(error) }}</p>
        <template v-else-if="listView">
          <div class="kb-card" style="padding: 0; overflow: auto">
            <table class="kb-graph-table">
              <caption class="kb-sr">图谱里的笔记与链接数</caption>
              <thead><tr><th scope="col">标题</th><th scope="col">知识库</th><th scope="col">类型</th><th scope="col" style="text-align: right">链接</th></tr></thead>
              <tbody>
                <tr v-for="n in listRows" :key="n.id">
                  <td><RouterLink :to="nodeHref(n)">{{ n.title }}</RouterLink></td>
                  <td><span style="display: inline-flex; align-items: center; gap: 6px"><span class="kb-dot is-small" :class="n.kind === 'note' ? `k-${n.space_kind}` : ''" aria-hidden="true" />{{ spaceName(n) }}</span></td>
                  <td>{{ n.kind === 'note' && n.type ? NOTE_TYPE_LABELS[n.type] : n.kind === 'task' ? '任务' : n.kind === 'project' ? '项目' : '收藏文档' }}</td>
                  <td class="kb-num">{{ n.degree }}</td>
                </tr>
              </tbody>
            </table>
            <p v-if="!listRows.length" class="kb-empty">没有符合条件的笔记。</p>
          </div>
        </template>
        <KbForceGraph
          v-else ref="graphRef" :graph="graph.data.value" :selected="selected" :query="query" :color-by="colorBy" :all-labels="allLabels" :orphans="orphans" :hidden="hidden" :focus-space="focus"
          @select="selected = $event" @open="open"
        >
          <div v-if="graph.isPending.value" class="kb-graph-empty" role="status">正在加载图谱…</div>
          <div v-else-if="!nodes.length" class="kb-graph-empty">
            <Waypoints aria-hidden="true" style="width: 32px; height: 32px" />
            <p style="margin: 0">还没有笔记。新建一篇，用 [[标题]] 链接到别的笔记，图谱就会长出来。</p>
            <RouterLink v-if="canEdit" to="/knowledge/notes/new" class="kb-btn is-primary"><Plus aria-hidden="true" />新建笔记</RouterLink>
          </div>
          <div v-if="nodes.length" class="kb-graph-legend" role="group" aria-label="按知识库类型显示">
            <button v-for="k in legend" :key="k.key" type="button" :aria-pressed="!hidden.includes(k.key)" @click="toggleKind(k.key)">
              <span class="kb-dot" :class="k.key === 'resource' ? '' : `k-${k.key}`" aria-hidden="true" />{{ k.label }}<span class="kb-num" style="color: var(--kb-muted)">{{ k.count }}</span>
            </button>
          </div>
          <p v-if="nodes.length" class="kb-graph-hint">滚轮缩放 · 拖动平移 · 点圆点看详情，双击或回车打开</p>
        </KbForceGraph>
        <p v-if="graph.data.value?.truncated" class="kb-meta" role="status" style="margin: 8px 0 0">笔记太多，图谱只画了其中 {{ nodes.filter(n => n.kind === 'note').length }} 篇（共 {{ graph.data.value.total_notes }} 篇）。可以在左栏只看一个知识库，或用列表视图。</p>
      </div>
    </main>

    <!-- 右栏：所选笔记 -->
    <aside class="kb-aside" aria-label="所选笔记">
      <template v-if="selectedNode">
        <div style="display: flex; flex-direction: column; gap: 8px">
          <span class="kb-chip" style="align-self: flex-start"><span class="kb-dot is-small" :class="selectedNode.kind === 'note' ? `k-${selectedNode.space_kind}` : ''" aria-hidden="true" />{{ selectedNode.kind === 'note' && selectedNode.space_kind ? SPACE_KIND_LABELS[selectedNode.space_kind] + ' · ' : '' }}{{ spaceName(selectedNode) }}</span>
          <h2 style="font-size: 19px; line-height: 1.35">{{ selectedNode.title }}</h2>
          <p v-if="note.data.value && excerpt(note.data.value.body)" style="margin: 0; color: var(--kb-muted); line-height: 1.6">{{ excerpt(note.data.value.body, 140) }}</p>
        </div>
        <dl v-if="note.data.value" class="kb-dl" style="grid-template-columns: 64px minmax(0, 1fr)">
          <dt>类型</dt><dd>{{ NOTE_TYPE_LABELS[note.data.value.type] }}</dd>
          <dt>作者</dt><dd>{{ authorLabel(note.data.value.updated_by) }}</dd>
          <dt>状态</dt>
          <dd style="display: flex; align-items: center; gap: 6px">
            <CircleCheck v-if="statusOf(note.data.value).key === 'confirmed'" aria-hidden="true" style="width: 15px; height: 15px; stroke: var(--kb-good)" />
            <CircleAlert v-else-if="statusOf(note.data.value).key === 'conflict'" aria-hidden="true" style="width: 15px; height: 15px; stroke: var(--kb-warning)" />
            <Info v-else aria-hidden="true" style="width: 15px; height: 15px; stroke: var(--kb-muted)" />
            {{ statusOf(note.data.value).label }}
          </dd>
          <dt>链接</dt><dd class="kb-num">链出 {{ note.data.value.links.length }} · 被链 {{ note.data.value.backlinks.length }}</dd>
        </dl>
        <p v-else-if="selectedNode.kind !== 'note'" class="kb-meta" style="margin: 0">{{ selectedNode.kind === 'task' ? '任务' : selectedNode.kind === 'project' ? '项目' : '收藏文档' }} · 被 {{ selectedNode.degree }} 篇笔记链接</p>
        <div style="display: flex; gap: 8px">
          <RouterLink :to="nodeHref(selectedNode)" class="kb-btn is-primary" style="flex: 1">{{ selectedNode.kind === 'note' ? '打开笔记' : '打开' }}</RouterLink>
          <button v-if="selectedNode.space_id" type="button" class="kb-btn" style="flex: 1" :aria-pressed="focus === selectedNode.space_id" @click="focus = focus === selectedNode.space_id ? null : selectedNode.space_id">{{ focus === selectedNode.space_id ? '显示全部' : '只看它的知识库' }}</button>
        </div>
        <div v-if="neighbors.length" class="kb-side-section">
          <h3 style="font-size: 13px; font-weight: 600">相连的笔记</h3>
          <div class="kb-list">
            <button v-for="m in neighbors" :key="m.id" type="button" class="kb-list-item" @click="selected = m.id" @dblclick="open(m)">
              <span class="kb-dot is-small" :class="m.kind === 'note' ? `k-${m.space_kind}` : ''" aria-hidden="true" />
              <span class="kb-grow">{{ m.title }}</span><span class="kb-meta">{{ spaceName(m) }}</span>
            </button>
          </div>
        </div>
      </template>
      <p v-else class="kb-meta">点图谱里的圆点，这里显示那篇笔记。</p>
      <p class="kb-meta kb-num kb-divider" style="margin: auto 0 0; padding-top: 12px">图谱里 {{ degreeShown.notes }} 篇笔记、{{ degreeShown.edges }} 条链接 · 孤立笔记 {{ degreeShown.orphans }} 篇</p>
    </aside>
  </div>
</template>
