<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY, type SimulationLinkDatum, type SimulationNodeDatum } from 'd3-force'
import type { ColorBy, GraphNode, GraphOut } from './api'
import { normalize } from './markdown'

// 知识图谱（T-0099，方案 6.2）：d3-force 计算布局（固定迭代次数、初始位置确定，同样的数据每次画得一样），
// SVG 画圆点和连线。滚轮 / 按钮缩放、拖动平移、悬停高亮相连的笔记、搜索定位。标题字号不随缩放变化。
const props = withDefaults(defineProps<{
  graph: GraphOut | undefined
  selected?: string | null
  query?: string
  colorBy?: ColorBy
  allLabels?: boolean
  orphans?: boolean
  hidden?: string[]
  focusSpace?: string | null
  center?: string | null
  compact?: boolean
  label?: string
}>(), { selected: null, query: '', colorBy: 'space', allLabels: false, orphans: true, hidden: () => [], focusSpace: null, center: null, compact: false, label: '知识图谱' })
const emit = defineEmits<{ select: [id: string]; open: [node: GraphNode] }>()

type Node = SimulationNodeDatum & { id: string; data: GraphNode; r: number; group: string }
const box = ref<HTMLElement | null>(null)
const size = ref({ w: 800, h: 560 })
const layout = shallowRef<{ nodes: Node[]; edges: { a: Node; b: Node }[] }>({ nodes: [], edges: [] })
const view = ref({ x: -400, y: -280, w: 800, h: 560 })
const hover = ref<string | null>(null)
const tip = ref<{ x: number; y: number; node: Node } | null>(null)

const groupOf = (n: GraphNode) => n.kind === 'note' ? (n.space_id ?? 'none') : 'resources'
const kindOf = (n: GraphNode) => n.kind === 'note' ? (n.space_kind ?? 'public') : 'resource'

function build() {
  const g = props.graph
  if (!g) { layout.value = { nodes: [], edges: [] }; return }
  const hidden = new Set(props.hidden)
  const linked = new Set<string>()
  for (const e of g.edges) { linked.add(e.source); linked.add(e.target) }
  const keep = g.nodes.filter(n => !hidden.has(kindOf(n)) && (props.orphans || linked.has(n.id) || n.id === props.center))
  const byId = new Map<string, Node>()
  // 按知识库分组：每组一个圆周上的中心，力把同组的笔记拉在一起（效果图里三类知识库各成一团）
  const groups = [...new Set(keep.map(groupOf))]
  const radius = props.compact ? 0 : Math.min(size.value.w, size.value.h) * 0.32
  const centers = new Map(groups.map((id, i) => [id, groups.length === 1 ? [0, 0] : [Math.cos((i / groups.length) * Math.PI * 2) * radius, Math.sin((i / groups.length) * Math.PI * 2) * radius]]))
  for (const n of keep) byId.set(n.id, { id: n.id, data: n, r: n.kind === 'note' ? 4 + Math.min(9, n.degree * 1.25) : 3.5, group: groupOf(n) })
  const nodes = [...byId.values()]
  const links: (SimulationLinkDatum<Node> & { a: Node; b: Node })[] = []
  for (const e of g.edges) {
    const a = byId.get(e.source), b = byId.get(e.target)
    if (a && b && a !== b) links.push({ source: a, target: b, a, b })
  }
  const sim = forceSimulation(nodes)
    .force('link', forceLink<Node, SimulationLinkDatum<Node>>(links).distance(props.compact ? 78 : 64).strength(0.6))
    .force('charge', forceManyBody<Node>().strength(props.compact ? -320 : -110).distanceMax(400))
    .force('collide', forceCollide<Node>(n => n.r + (props.compact ? 22 : 6)))
    .force('x', forceX<Node>(n => centers.get(n.group)?.[0] ?? 0).strength(props.compact ? 0.08 : 0.05))
    .force('y', forceY<Node>(n => centers.get(n.group)?.[1] ?? 0).strength(props.compact ? 0.08 : 0.05))
    .stop()
  if (props.center) { const c = byId.get(props.center); if (c) { c.fx = 0; c.fy = 0 } }
  const ticks = Math.min(400, 120 + nodes.length * 2)
  for (let i = 0; i < ticks; i++) sim.tick()
  layout.value = { nodes, edges: links.map(l => ({ a: l.a, b: l.b })) }
  fit()
}

function fit() {
  const nodes = layout.value.nodes
  const { w, h } = size.value
  if (!nodes.length) { view.value = { x: -w / 2, y: -h / 2, w, h }; return }
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity
  for (const n of nodes) { x0 = Math.min(x0, n.x! - n.r); x1 = Math.max(x1, n.x! + n.r); y0 = Math.min(y0, n.y! - n.r); y1 = Math.max(y1, n.y! + n.r) }
  const pad = props.compact ? 34 : 60
  let vw = Math.max(x1 - x0 + pad * 2, props.compact ? 160 : 240), vh = Math.max(y1 - y0 + pad * 2, props.compact ? 120 : 200)
  // 保持容器的宽高比，免得 meet 留白算错
  const ratio = w / h
  if (vw / vh > ratio) vh = vw / ratio; else vw = vh * ratio
  view.value = { x: (x0 + x1) / 2 - vw / 2, y: (y0 + y1) / 2 - vh / 2, w: vw, h: vh }
}

function zoom(factor: number, cx?: number, cy?: number) {
  const v = view.value
  const w = Math.min(Math.max(v.w * factor, 80), 20000), h = w * (v.h / v.w)
  const px = cx ?? v.x + v.w / 2, py = cy ?? v.y + v.h / 2
  view.value = { x: px - (px - v.x) * (w / v.w), y: py - (py - v.y) * (h / v.h), w, h }
}
defineExpose({ zoomIn: () => zoom(1 / 1.25), zoomOut: () => zoom(1.25), fit })

const scale = computed(() => size.value.w / view.value.w)
const toUser = (event: { clientX: number; clientY: number }) => {
  const rect = box.value!.getBoundingClientRect()
  return { x: view.value.x + ((event.clientX - rect.left) / rect.width) * view.value.w, y: view.value.y + ((event.clientY - rect.top) / rect.height) * view.value.h }
}
function onWheel(event: WheelEvent) {
  event.preventDefault()
  const p = toUser(event)
  zoom(event.deltaY > 0 ? 1.12 : 1 / 1.12, p.x, p.y)
}
let pan: { id: number; x: number; y: number; vx: number; vy: number; moved: boolean } | null = null
const panning = ref(false)
function onDown(event: PointerEvent) {
  if ((event.target as Element).closest('.kb-node')) return
  pan = { id: event.pointerId, x: event.clientX, y: event.clientY, vx: view.value.x, vy: view.value.y, moved: false }
  ;(event.currentTarget as Element).setPointerCapture?.(event.pointerId)
}
function onMove(event: PointerEvent) {
  if (!pan || pan.id !== event.pointerId) return
  const rect = box.value!.getBoundingClientRect()
  const dx = ((event.clientX - pan.x) / rect.width) * view.value.w, dy = ((event.clientY - pan.y) / rect.height) * view.value.h
  if (Math.abs(event.clientX - pan.x) + Math.abs(event.clientY - pan.y) > 3) { pan.moved = true; panning.value = true }
  view.value = { ...view.value, x: pan.vx - dx, y: pan.vy - dy }
}
function onUp() { pan = null; panning.value = false }

const neighbors = computed(() => {
  const map = new Map<string, Set<string>>()
  for (const { a, b } of layout.value.edges) {
    if (!map.has(a.id)) map.set(a.id, new Set())
    if (!map.has(b.id)) map.set(b.id, new Set())
    map.get(a.id)!.add(b.id); map.get(b.id)!.add(a.id)
  }
  return map
})
const q = computed(() => normalize(props.query))
const matches = (n: Node) => !!q.value && normalize(n.data.title).includes(q.value)
const fillClass = (n: Node) => {
  if (n.data.kind !== 'note') return 'f-resource'
  if (props.colorBy === 'author') return n.data.author_kind === 'agent' ? 'f-agent' : n.data.author_kind === 'system' ? 'f-resource' : 'f-user'
  if (props.colorBy === 'confirmed') return n.data.confirmed || n.data.author_kind === 'user' ? 'f-confirmed' : 'f-pending'
  return `f-${n.data.space_kind ?? 'public'}`
}
const drawn = computed(() => {
  const hv = hover.value, near = hv ? neighbors.value.get(hv) : null
  return layout.value.nodes.map((n) => {
    const isMatch = matches(n)
    let dim = false
    if (hv) dim = n.id !== hv && !near?.has(n.id)
    else if (q.value) dim = !isMatch
    else if (props.focusSpace) dim = n.data.space_id !== props.focusSpace
    const strong = n.id === props.selected || n.id === hv || n.id === props.center
    const label = props.compact || props.allLabels || n.data.degree >= 5 || strong || !!near?.has(n.id) || isMatch || (!!props.focusSpace && n.data.space_id === props.focusSpace && n.data.degree >= 2)
    return { n, dim, strong, label, hub: n.data.degree >= 7, fill: fillClass(n) }
  })
})
const lines = computed(() => {
  const hv = hover.value
  return layout.value.edges.map(({ a, b }) => ({ key: a.id + '>' + b.id, a, b, hi: !!hv && (a.id === hv || b.id === hv), dim: (!!hv && a.id !== hv && b.id !== hv) || (!hv && !!q.value) }))
})
const focusable = computed(() => layout.value.nodes.length <= 250)
const ariaFor = (n: Node) => `${n.data.title}，${n.data.kind === 'note' ? '笔记' : n.data.kind === 'task' ? '任务' : n.data.kind === 'project' ? '项目' : '收藏文档'}，${n.data.degree} 条链接`

function enter(n: Node) {
  hover.value = n.id
  if (!box.value) return
  const rect = box.value.getBoundingClientRect()
  tip.value = { x: ((n.x! - view.value.x) / view.value.w) * rect.width, y: ((n.y! - view.value.y) / view.value.h) * rect.height, node: n }
}
function leave() { hover.value = null; tip.value = null }
function pick(n: Node) { emit('select', n.id) }
function key(event: KeyboardEvent, n: Node) {
  if (event.key === 'Enter') { event.preventDefault(); emit('open', n.data) }
  else if (event.key === ' ') { event.preventDefault(); emit('select', n.id) }
}
function dbl(n: Node) { emit('open', n.data) }

let observer: ResizeObserver | null = null
onMounted(() => {
  if (box.value) {
    const rect = box.value.getBoundingClientRect()
    if (rect.width && rect.height) size.value = { w: rect.width, h: rect.height }
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(([entry]) => {
        const { width, height } = entry.contentRect
        if (!width || !height || (Math.abs(width - size.value.w) < 1 && Math.abs(height - size.value.h) < 1)) return
        size.value = { w: width, h: height }
        fit()
      })
      observer.observe(box.value)
    }
  }
  build()
})
onBeforeUnmount(() => observer?.disconnect())
watch(() => [props.graph, props.orphans, props.hidden.join(','), props.center], build)
// 搜索定位：只有一个匹配时平移到它
watch(q, (value) => {
  if (!value) return
  const found = layout.value.nodes.filter(matches)
  if (found.length === 1) view.value = { ...view.value, x: found[0].x! - view.value.w / 2, y: found[0].y! - view.value.h / 2 }
})
</script>

<template>
  <div ref="box" class="kb-graph" @pointerleave="leave">
    <svg
      class="kb-graph-svg" :class="{ 'is-panning': panning }" :viewBox="`${view.x} ${view.y} ${view.w} ${view.h}`" preserveAspectRatio="xMidYMid meet" role="group" :aria-label="label"
      @wheel="onWheel" @pointerdown="onDown" @pointermove="onMove" @pointerup="onUp" @pointercancel="onUp"
    >
      <g aria-hidden="true">
        <line v-for="l in lines" :key="l.key" class="kb-edge" :class="{ 'is-hi': l.hi, 'is-dim': l.dim }" :x1="l.a.x" :y1="l.a.y" :x2="l.b.x" :y2="l.b.y" />
      </g>
      <g
        v-for="d in drawn" :key="d.n.id" class="kb-node" :class="{ 'is-dim': d.dim, 'is-strong': d.strong, 'is-hub': d.hub, 'is-selected': d.n.id === selected }"
        :transform="`translate(${d.n.x},${d.n.y})`" :tabindex="focusable ? 0 : -1" role="button" :aria-label="ariaFor(d.n)" :aria-pressed="d.n.id === selected"
        @click="pick(d.n)" @dblclick="dbl(d.n)" @keydown="key($event, d.n)" @pointerenter="enter(d.n)" @focus="enter(d.n)" @blur="leave"
      >
        <circle :r="d.n.r" :class="d.fill" />
        <circle :r="Math.max(d.n.r + 6, 12 / scale)" fill="transparent" stroke="none" />
        <text v-if="d.label" :y="d.n.r + 13 / scale" text-anchor="middle" :style="{ fontSize: `${(d.hub ? 13 : 11) / scale}px`, strokeWidth: `${3 / scale}px` }">{{ d.n.data.title }}</text>
      </g>
    </svg>
    <div v-if="tip && !compact" class="kb-graph-tip" :style="{ left: `${Math.min(tip.x + 14, size.w - 250)}px`, top: `${Math.max(tip.y - 10, 8)}px` }" role="status">
      <strong>{{ tip.node.data.title }}</strong>
      <span class="kb-meta">{{ tip.node.data.kind === 'note' ? '笔记' : tip.node.data.kind === 'task' ? '任务' : tip.node.data.kind === 'project' ? '项目' : '收藏文档' }} · {{ tip.node.data.degree }} 条链接</span>
    </div>
    <slot />
  </div>
</template>
