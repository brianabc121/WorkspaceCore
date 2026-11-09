<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { List, Maximize, Minus, Plus, RefreshCw, Waypoints } from '@lucide/vue'
import { ApiError } from '../../services/api'
import { NOTE_TYPE_LABELS, SPACE_KIND_LABELS, TYPE_GROUPS, kbError, typeGroupOf, useLocalGraph, type ColorBy, type GraphNode, type Space } from './api'
import { nodeHref } from './format'
import KbForceGraph from './KbForceGraph.vue'

// 知识总览的局部关联（T-0173，规范 4.1、4.3）：以选中的笔记为中心，调用现有的局部图谱（depth=1），
// 不下载全量图谱。图例只数当前显示的这些节点；“列表”是图的文字替代。点另一篇笔记 → 发出 select，由总览换中心；
// 双击或回车打开。不做写操作。
const props = defineProps<{ scope: string; noteId: string | null; noteTitle?: string; total?: number; scopeLabel: string; spaces: Space[] }>()
const emit = defineEmits<{ select: [id: string]; open: [node: GraphNode] }>()
const id = computed(() => props.noteId)
const number = new Intl.NumberFormat('zh-CN')
const local = useLocalGraph(props.scope, id, ref(1))
const graph = computed(() => (props.noteId ? local.data.value : undefined))
const nodes = computed(() => graph.value?.nodes ?? [])
const noteNodes = computed(() => nodes.value.filter(n => n.kind === 'note'))
const colorBy = ref<ColorBy>('type'), listView = ref(false)
const graphRef = ref<InstanceType<typeof KbForceGraph> | null>(null)
const spaceName = (n: GraphNode) => (n.space_id ? props.spaces.find(s => s.id === n.space_id)?.name : undefined) ?? (n.space_kind ? SPACE_KIND_LABELS[n.space_kind] : '')
const kindLabel = (n: GraphNode) => n.kind === 'note' ? (n.type ? NOTE_TYPE_LABELS[n.type] : '笔记') : n.kind === 'task' ? '任务' : n.kind === 'project' ? '项目' : n.kind === 'file' ? '图片' : '收藏文档'
const gone = computed(() => local.error.value instanceof ApiError && (local.error.value.status === 404 || local.error.value.status === 403))

// 图例：现有的 6 组类型（或知识库类型、作者），只数当前显示的节点
type Legend = { key: string; dot: string; label: string; count: number }
const legend = computed<Legend[]>(() => {
  const notes = noteNodes.value
  const others = nodes.value.length - notes.length
  const rest: Legend[] = others ? [{ key: 'resource', dot: '', label: '任务与文档', count: others }] : []
  if (colorBy.value === 'space') return [...(['project', 'agent', 'public'] as const).map(k => ({ key: k, dot: `k-${k}`, label: SPACE_KIND_LABELS[k], count: notes.filter(n => (n.space_kind ?? 'public') === k).length })).filter(k => k.count), ...rest]
  if (colorBy.value === 'author') return [
    { key: 'user', dot: 'k-user', label: '人', count: notes.filter(n => n.author_kind === 'user').length },
    { key: 'agent', dot: 'k-agent', label: '智能体', count: notes.filter(n => n.author_kind === 'agent').length },
    { key: 'system', dot: '', label: '工作台', count: notes.filter(n => n.author_kind === 'system').length },
  ].filter(k => k.count).concat(rest)
  return [...TYPE_GROUPS.map(g => ({ key: g.key, dot: `g-${g.key}`, label: g.label, count: notes.filter(n => typeGroupOf(n.type) === g.key).length })).filter(k => k.count), ...rest]
})
function dotClass(n: GraphNode) {
  if (n.kind === 'file') return 'is-file'
  if (n.kind !== 'note') return ''
  if (colorBy.value === 'space') return `k-${n.space_kind ?? 'public'}`
  if (colorBy.value === 'author') return n.author_kind === 'agent' ? 'k-agent' : n.author_kind === 'user' ? 'k-user' : ''
  return `g-${typeGroupOf(n.type)}`
}
const listRows = computed(() => [...nodes.value].sort((a, b) => (a.id === props.noteId ? -1 : b.id === props.noteId ? 1 : b.degree - a.degree)))
function pick(target: string) {
  const node = nodes.value.find(n => n.id === target)
  if (node?.kind === 'note' && target !== props.noteId) emit('select', target)
}
</script>

<template>
  <section class="kb-card kb-ov-graph" aria-labelledby="kb-ov-graph-title">
    <div class="kb-ov-card-head">
      <div class="kb-stack">
        <h2 id="kb-ov-graph-title">知识关联</h2>
        <p class="kb-meta">
          <span class="kb-ov-badge">当前关联</span>
          <template v-if="noteId && graph">以《{{ noteTitle ?? '所选笔记' }}》为中心的一层关联 · {{ noteNodes.length }} 篇笔记</template>
        </p>
        <p class="kb-meta kb-ov-graph-scope">{{ total === undefined ? '只画与它直接相连的笔记' : `只画与它直接相连的笔记；${scopeLabel}共 ${number.format(total)} 篇` }}</p>
      </div>
      <div class="kb-ov-tools">
        <label class="kb-check kb-meta">着色
          <select v-model="colorBy" class="kb-select">
            <option value="type">按类型</option><option value="space">按知识库</option><option value="author">按作者</option>
          </select>
        </label>
        <button type="button" class="kb-btn is-small" :aria-pressed="listView" @click="listView = !listView"><List aria-hidden="true" />列表</button>
        <div v-if="!listView" class="kb-zoom" role="group" aria-label="缩放">
          <button type="button" aria-label="缩小" @click="graphRef?.zoomOut()"><Minus aria-hidden="true" class="kb-ov-zoom-icon" /></button>
          <button type="button" aria-label="适应窗口" @click="graphRef?.fit()"><Maximize aria-hidden="true" class="kb-ov-zoom-icon" /></button>
          <button type="button" aria-label="放大" @click="graphRef?.zoomIn()"><Plus aria-hidden="true" class="kb-ov-zoom-icon" /></button>
        </div>
      </div>
    </div>

    <p v-if="!noteId" class="kb-ov-graph-state">这个范围里还没有笔记，没有可以显示的关联。</p>
    <p v-else-if="gone" class="kb-ov-graph-state" role="status">这篇笔记已删除或你没有权限查看，关联不再显示。</p>
    <p v-else-if="local.error.value" class="kb-error" role="alert">
      关联没有读到：{{ kbError(local.error.value) }}
      <button type="button" class="kb-btn is-small" @click="local.refetch()"><RefreshCw aria-hidden="true" />重试</button>
    </p>
    <template v-else-if="listView">
      <ul class="kb-ov-graph-list" aria-label="当前关联的笔记">
        <li v-for="n in listRows" :key="n.id">
          <span class="kb-dot is-small" :class="dotClass(n)" aria-hidden="true" />
          <button v-if="n.kind === 'note' && n.id !== noteId" type="button" class="kb-ov-link" @click="pick(n.id)">{{ n.title }}</button>
          <strong v-else-if="n.id === noteId">{{ n.title }}<span class="kb-meta">（中心）</span></strong>
          <RouterLink v-else-if="n.kind !== 'file'" :to="nodeHref(n)">{{ n.title }}</RouterLink>
          <a v-else :href="nodeHref(n)" target="_blank" rel="noopener">{{ n.title }}</a>
          <span class="kb-meta kb-grow">{{ kindLabel(n) }}{{ n.kind === 'note' ? ' · ' + spaceName(n) : '' }}</span>
          <span class="kb-meta kb-num">{{ n.degree }} 条</span>
        </li>
      </ul>
      <p v-if="local.isPending.value" class="kb-meta" role="status">正在加载关联…</p>
    </template>
    <template v-else>
      <div class="kb-ov-graph-box">
        <KbForceGraph ref="graphRef" :graph="graph" :center="noteId" :selected="noteId" :color-by="colorBy" compact :label="`《${noteTitle ?? '所选笔记'}》和直接相连的笔记`" @select="pick" @open="emit('open', $event)">
          <div v-if="local.isPending.value" class="kb-graph-empty" role="status">正在加载关联…</div>
          <div v-else-if="nodes.length <= 1" class="kb-graph-empty kb-ov-graph-alone"><Waypoints aria-hidden="true" /><p>这篇笔记还没有和别的笔记关联。用 [[标题]] 链接后会出现在这里。</p></div>
        </KbForceGraph>
      </div>
    </template>

    <div class="kb-ov-graph-foot">
      <div v-if="legend.length" class="kb-ov-legend" role="list" aria-label="图例（当前关联）">
        <span v-for="k in legend" :key="k.key" role="listitem"><span class="kb-dot is-small" :class="k.dot" aria-hidden="true" />{{ k.label }}<span class="kb-num kb-meta">{{ k.count }}</span></span>
      </div>
      <RouterLink to="/knowledge/graph" class="kb-btn is-small kb-ov-full"><Waypoints aria-hidden="true" />完整图谱</RouterLink>
    </div>
  </section>
</template>
