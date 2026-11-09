<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import type { Schema } from '../../services/api'
import { formatTime } from '../time'
import { RANGE_LABELS, formatMetricValue, niceTicks, stepLabel } from './metricFormat'

// 一个预设指标视图的折线图（T-0177，二批改造规范第 7.2 节）：轻量 SVG，不引入图表库。
// - 每条序列独立绘制、各自保留 label；不跨序列求平均（多个服务的 P95 各画各的）。
// - null 是缺测：线在那里断开，不补 0、不插值；最新一个点缺测时“当前”显示“—”。
// - 纵轴刻度带单位（formatMetricValue），横轴按接口返回的时间范围与步长；附数据表作为文字替代。
const props = defineProps<{ view: Schema['MetricViewOut']; range: Schema['MetricsOut']['range']; stepSeconds: number }>()

const HEIGHT = 190, TOP = 12, BOTTOM = 26, RIGHT = 14
const box = ref<HTMLElement | null>(null), width = ref(640)
let observer: ResizeObserver | null = null
onMounted(() => {
  if (!box.value || typeof ResizeObserver === 'undefined') return
  const measure = () => { const w = box.value?.clientWidth ?? 0; if (w > 0) width.value = Math.max(260, Math.round(w)) }
  measure()
  observer = new ResizeObserver(measure)
  observer.observe(box.value)
})
onBeforeUnmount(() => observer?.disconnect())

const multi = computed(() => props.view.series.length > 1)
const valid = (value: number | null): value is number => value !== null && Number.isFinite(value)
const series = computed(() => props.view.series.map((item, index) => {
  const last = item.points.at(-1)
  return {
    key: `${index}:${item.label}`,
    label: item.label || props.view.title,
    color: `var(--chart-${(index % 8) + 1})`,
    points: item.points.map((point) => ({ t: Date.parse(point.at), value: point.value })).filter((point) => Number.isFinite(point.t)),
    current: formatMetricValue(last ? last.value : null, props.view.unit),
  }
}))
const values = computed(() => series.value.flatMap((item) => item.points.map((point) => point.value).filter(valid)))
const times = computed(() => [...new Set(series.value.flatMap((item) => item.points.map((point) => point.t)))].sort((a, b) => a - b))
const hasData = computed(() => values.value.length > 0)

const ticks = computed(() => {
  if (!hasData.value) return []
  return niceTicks(Math.min(0, ...values.value), Math.max(...values.value), props.view.unit)
})
const tickLabels = computed(() => ticks.value.map((tick) => formatMetricValue(tick, props.view.unit)))
const left = computed(() => Math.min(96, Math.max(40, Math.max(0, ...tickLabels.value.map((label) => label.length)) * 6.6 + 12)))
const low = computed(() => ticks.value[0] ?? 0)
const high = computed(() => ticks.value.at(-1) ?? 1)
const t0 = computed(() => times.value[0] ?? 0)
const t1 = computed(() => times.value.at(-1) ?? 0)
const x = (t: number) => (t1.value > t0.value ? left.value + ((t - t0.value) / (t1.value - t0.value)) * (width.value - left.value - RIGHT) : (left.value + width.value - RIGHT) / 2)
const y = (value: number) => TOP + (1 - (value - low.value) / ((high.value - low.value) || 1)) * (HEIGHT - TOP - BOTTOM)

// 每条序列按缺测切成若干段；只有一个点的段画成圆点，免得看不见
const plotted = computed(() => series.value.map((item) => {
  const segments: { x: number; y: number }[][] = []
  let current: { x: number; y: number }[] = []
  for (const point of item.points) {
    if (!valid(point.value)) { if (current.length) segments.push(current); current = []; continue }
    current.push({ x: x(point.t), y: y(point.value) })
  }
  if (current.length) segments.push(current)
  return { ...item, segments: segments.map((points) => ({ d: points.map((p, i) => `${i ? 'L' : 'M'}${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' '), single: points.length === 1 ? points[0]! : null })) }
}))

// 横轴刻度：按范围取整点（10 分钟 / 1 小时 / 4 小时 / 1 天），太密时隔几个取一个
const INTERVALS: Record<Schema['MetricsOut']['range'], number> = { '1h': 10 * 60_000, '6h': 3_600_000, '24h': 4 * 3_600_000, '7d': 86_400_000 }
const timeFormat = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
const dayFormat = new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric' })
const xTicks = computed(() => {
  if (!hasData.value || t1.value <= t0.value) return []
  const interval = INTERVALS[props.range]
  const start = new Date(t0.value)
  if (interval >= 86_400_000) start.setHours(0, 0, 0, 0)
  else if (interval >= 3_600_000) start.setHours(Math.floor(start.getHours() / (interval / 3_600_000)) * (interval / 3_600_000), 0, 0, 0)
  else start.setMinutes(Math.floor(start.getMinutes() / (interval / 60_000)) * (interval / 60_000), 0, 0)
  const found: number[] = []
  for (let t = start.getTime(); t <= t1.value; t += interval) if (t >= t0.value) found.push(t)
  const room = Math.max(2, Math.floor((width.value - left.value - RIGHT) / 64))
  const every = Math.ceil(found.length / room)
  return found.filter((_, i) => i % every === 0).map((t) => ({ t, x: x(t), label: interval >= 86_400_000 ? dayFormat.format(t) : timeFormat.format(t) }))
})

const description = computed(() => `${props.view.title}趋势图，${RANGE_LABELS[props.range]}，${series.value.length} 条序列；缺测处断开，不补 0`)

// 悬停：取最近的采样时间，显示每条序列在那一刻的值
const hover = ref<number | null>(null)
function onMove(event: PointerEvent) {
  const svg = event.currentTarget as SVGSVGElement
  const rect = svg.getBoundingClientRect()
  if (!rect.width || !times.value.length) return
  const px = ((event.clientX - rect.left) / rect.width) * width.value
  let best = 0
  for (let i = 1; i < times.value.length; i++) if (Math.abs(x(times.value[i]!) - px) < Math.abs(x(times.value[best]!) - px)) best = i
  hover.value = times.value[best]!
}
const hovered = computed(() => {
  const t = hover.value
  if (t === null) return null
  const rows = series.value.map((item) => {
    const point = item.points.find((p) => p.t === t)
    return { key: item.key, label: item.label, color: item.color, value: point && valid(point.value) ? point.value : null }
  })
  const px = x(t)
  return { t, x: px, rows, side: px > width.value / 2 ? 'left' : 'right' }
})

// 数据表：所有采样时间 × 每条序列；缺测写“—”
const table = computed(() => times.value.map((t) => ({
  t, at: new Date(t).toISOString(),
  cells: series.value.map((item) => formatMetricValue(item.points.find((p) => p.t === t)?.value ?? null, props.view.unit)),
})))
</script>

<template>
  <section class="ws-panel ops-metric" role="region" :aria-label="view.title">
    <header class="ops-metric-head">
      <h3>{{ view.title }}</h3>
      <span class="ops-metric-range">{{ RANGE_LABELS[range] }} · 每 {{ stepLabel(stepSeconds) }}一个点</span>
    </header>
    <p v-if="!view.series.length" class="ops-metric-empty">此视图暂无采样数据。</p>
    <template v-else>
      <ul class="ops-metric-current" :aria-label="`${view.title} 当前值`">
        <li v-for="item in series" :key="item.key">
          <span v-if="multi" class="ops-swatch" :style="{ background: item.color }" aria-hidden="true"></span>
          <span class="ops-metric-label">{{ multi ? item.label : '当前' }}</span>
          <strong class="ops-metric-value">{{ item.current }}</strong>
        </li>
      </ul>
      <div ref="box" class="ops-chart">
        <svg v-if="hasData" class="ops-chart-svg" :viewBox="`0 0 ${width} ${HEIGHT}`" :height="HEIGHT" role="img" :aria-label="description" @pointermove="onMove" @pointerleave="hover = null">
          <g class="ops-grid">
            <g v-for="(tick, index) in ticks" :key="tick">
              <line :x1="left" :x2="width - RIGHT" :y1="y(tick)" :y2="y(tick)" :class="{ 'is-base': index === 0 }" />
              <text :x="left - 8" :y="y(tick)" dy="0.32em" text-anchor="end">{{ tickLabels[index] }}</text>
            </g>
            <text v-for="tick in xTicks" :key="tick.t" :x="tick.x" :y="HEIGHT - 8" text-anchor="middle">{{ tick.label }}</text>
          </g>
          <g v-for="item in plotted" :key="item.key" class="ops-series" :style="{ color: item.color }" :data-series="item.label">
            <template v-for="(segment, index) in item.segments" :key="index">
              <circle v-if="segment.single" data-segment :cx="segment.single.x" :cy="segment.single.y" r="3.5" />
              <path v-else data-segment :d="segment.d" />
            </template>
          </g>
          <g v-if="hovered" class="ops-hover">
            <line :x1="hovered.x" :x2="hovered.x" :y1="TOP" :y2="HEIGHT - BOTTOM" />
            <template v-for="row in hovered.rows" :key="row.key">
              <circle v-if="row.value !== null" :cx="hovered.x" :cy="y(row.value)" r="4" :style="{ fill: row.color }" />
            </template>
          </g>
        </svg>
        <p v-else class="ops-metric-empty">当前时间范围暂无有效采样（全部缺测）。</p>
        <div v-if="hovered" class="ops-tooltip" :class="`is-${hovered.side}`" :style="{ left: `${(hovered.x / width) * 100}%` }" aria-hidden="true">
          <time>{{ formatTime(new Date(hovered.t).toISOString()) }}</time>
          <span v-for="row in hovered.rows" :key="row.key"><i v-if="multi" class="ops-swatch" :style="{ background: row.color }"></i>{{ multi ? `${row.label}：` : '' }}{{ formatMetricValue(row.value, view.unit) }}</span>
        </div>
      </div>
      <ul v-if="multi" class="ops-legend" :aria-label="`${view.title} 图例`">
        <li v-for="item in series" :key="item.key"><span class="ops-swatch" :style="{ background: item.color }" aria-hidden="true"></span>{{ item.label }}</li>
      </ul>
      <details class="ops-data">
        <summary>数据表（“—”为缺测）</summary>
        <div class="ws-table-wrap ops-data-wrap" tabindex="0" :aria-label="`${view.title} 数据表滚动区`">
          <table class="ops-data-table">
            <caption>{{ view.title }} · {{ RANGE_LABELS[range] }}</caption>
            <thead><tr><th scope="col">采样时间</th><th v-for="item in series" :key="item.key" scope="col">{{ item.label }}</th></tr></thead>
            <tbody>
              <tr v-for="row in table" :key="row.t"><th scope="row"><time :datetime="row.at">{{ formatTime(row.at) }}</time></th><td v-for="(cell, index) in row.cells" :key="index">{{ cell }}</td></tr>
            </tbody>
          </table>
        </div>
      </details>
    </template>
  </section>
</template>
