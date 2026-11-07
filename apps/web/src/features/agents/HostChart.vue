<script setup lang="ts">
import { computed, ref } from 'vue'
import { RANGE, RANGE_HOURS, bytes, hourLabel, load, percent, rate, type HostRange } from './host'

// 服务器状态的趋势图（T-0113）：每小时一个点。按 dataviz 规范：一张图一条轴（CPU 使用率和负载分两张图），
// 两个系列用固定顺序的两种颜色（--chart-1 蓝、--chart-2 橙，亮暗两套都跑过色觉检查），第二个系列再加虚线，
// 不只靠颜色区分；有图例；缺数据的小时断开不连线；悬停或键盘左右键逐小时看数值；“以表格查看”给读屏和不看颜色的人。
// 整机与单个智能体的趋势共用这张图（T-0147）：点是每小时一个，键是数值字段名
type Row = { at: string } & { [key: string]: string | number | null | undefined }
export type HostSeries = { key: string; label: string }
const props = defineProps<{ title: string; note?: string; kind: 'percent' | 'load' | 'rate' | 'bytes'; series: HostSeries[]; extra?: HostSeries[]; points: Row[]; range: HostRange }>()
const num = (r: Row | null | undefined, key: string): number | null => { const v = r?.[key]; return typeof v === 'number' ? v : null }

const HOUR = 3_600_000, W = 1000
const format = (value: number | null | undefined) => props.kind === 'percent' ? percent(value) : props.kind === 'load' ? load(value) : props.kind === 'bytes' ? bytes(value) : rate(value)
const rows = computed(() => [...props.points].map(p => ({ ...p, t: Date.parse(p.at) })).sort((a, b) => a.t - b.t))
const end = computed(() => Math.max(Math.ceil(Date.now() / HOUR) * HOUR, (rows.value.at(-1)?.t ?? 0) + HOUR))
const start = computed(() => end.value - RANGE_HOURS[props.range] * HOUR)
const x = (t: number) => ((t - start.value) / (end.value - start.value)) * W
const values = computed(() => rows.value.flatMap(r => props.series.map(s => num(r, s.key))).filter((v): v is number => v !== null))

// 轴的最大值：百分比固定 100；其余取 1、2、5 × 10ⁿ 里不小于最大值的那个（网速、字节数先换成 KB、MB…再取）
function nice(max: number): number {
  const p = 10 ** Math.floor(Math.log10(max))
  return [1, 2, 5, 10].map(f => f * p).find(v => v >= max) ?? 10 * p
}
const top = computed(() => {
  if (props.kind === 'percent') return 100
  const max = Math.max(0, ...values.value)
  if (props.kind === 'load') return max <= 1 ? 1 : nice(max)
  let base = 1
  while (max / base >= 1024) base *= 1024
  return max <= 0 ? 1024 : nice(max / base) * base
})
const ticks = computed(() => [top.value, top.value / 2, 0])
const y = (v: number) => (1 - Math.min(v, top.value) / top.value) * 100

// 每个系列按“连续的小时”分段；只有一个点的段画成圆点
const segments = computed(() => props.series.map(s => {
  const out: { t: number; v: number }[][] = []
  let current: { t: number; v: number }[] = [], last = -Infinity
  for (const r of rows.value) {
    const v = num(r, s.key)
    if (v === null || r.t - last > HOUR + 60_000) { if (current.length) out.push(current); current = [] }
    if (v !== null) current.push({ t: r.t, v })
    last = r.t
  }
  if (current.length) out.push(current)
  return out
}))
const line = (segment: { t: number; v: number }[]) => segment.map(p => `${x(p.t).toFixed(1)},${y(p.v).toFixed(2)}`).join(' ')

const axisLabel = (t: number) => props.range === '24h' ? hourLabel(new Date(t).toISOString(), false) : `${new Date(t).getMonth() + 1}/${new Date(t).getDate()}`
const xTicks = computed(() => [start.value, (start.value + end.value) / 2, end.value].map(axisLabel))

const active = ref<number | null>(null)
const point = computed(() => active.value === null ? null : rows.value[active.value] ?? null)
const left = (t: number) => `${(x(t) / W) * 100}%`
function nearest(t: number): number {
  let best = 0
  rows.value.forEach((r, i) => { if (Math.abs(r.t - t) < Math.abs(rows.value[best].t - t)) best = i })
  return best
}
function hover(event: PointerEvent) {
  if (!rows.value.length) return
  const box = (event.currentTarget as HTMLElement).getBoundingClientRect()
  active.value = nearest(start.value + ((event.clientX - box.left) / Math.max(box.width, 1)) * (end.value - start.value))
}
function key(event: KeyboardEvent) {
  const n = rows.value.length
  if (!n) return
  const i = active.value ?? n - 1
  const next = { ArrowLeft: i - 1, ArrowRight: i + 1, Home: 0, End: n - 1 }[event.key]
  if (next === undefined) return
  event.preventDefault()
  active.value = Math.min(n - 1, Math.max(0, next))
}
const tipStyle = computed(() => {
  if (!point.value) return {}
  const at = x(point.value.t) / W
  return at < 0.5 ? { left: `calc(${at * 100}% + 10px)` } : { left: `calc(${at * 100}% - 10px)`, transform: 'translateX(-100%)' }
})
const summary = computed(() => {
  const last = rows.value.at(-1)
  return `${props.title}，最近 ${RANGE[props.range]}，每小时一个点。` + (last ? `最近一小时 ${props.series.map(s => `${s.label} ${format(num(last, s.key))}`).join('，')}。` : '') + '用左右方向键逐小时查看。'
})
const tableOpen = ref(false)
const tableRows = computed(() => tableOpen.value ? [...rows.value].reverse() : [])
</script>

<template>
  <figure class="host-chart">
    <figcaption class="host-chart-head">
      <span><strong>{{ title }}</strong><span v-if="note" class="form-help">{{ note }}</span></span>
      <span v-if="series.length > 1" class="host-chart-legend">
        <span v-for="(s, i) in series" :key="s.key"><span class="host-chart-swatch" :class="`is-${i + 1}`" aria-hidden="true" />{{ s.label }}</span>
      </span>
    </figcaption>
    <p v-if="!values.length" class="form-help host-chart-empty">这段时间还没有数据。</p>
    <div v-else class="host-chart-body">
      <div class="host-chart-y" aria-hidden="true"><span v-for="t in ticks" :key="t">{{ kind === 'percent' ? `${t}%` : format(t) }}</span></div>
      <div class="host-chart-plot" tabindex="0" role="group" :aria-label="summary" @pointermove="hover" @pointerleave="active = null" @focus="active = rows.length - 1" @blur="active = null" @keydown="key">
        <div class="host-chart-grid" aria-hidden="true"><span v-for="t in ticks" :key="t" /></div>
        <svg class="host-chart-svg" :viewBox="`0 0 ${W} 100`" preserveAspectRatio="none" aria-hidden="true">
          <g v-for="(list, i) in segments" :key="series[i].key" :class="`is-${i + 1}`">
            <polyline v-for="(segment, k) in list.filter(s => s.length > 1)" :key="k" :points="line(segment)" />
          </g>
        </svg>
        <template v-for="(list, i) in segments" :key="`dots-${series[i].key}`">
          <span v-for="segment in list.filter(s => s.length === 1)" :key="segment[0].t" class="host-chart-dot" :class="`is-${i + 1}`" :style="{ left: left(segment[0].t), top: `${y(segment[0].v)}%` }" aria-hidden="true" />
        </template>
        <template v-if="point">
          <span class="host-chart-cross" :style="{ left: left(point.t) }" aria-hidden="true" />
          <template v-for="(s, i) in series" :key="`mark-${s.key}`">
            <span v-if="num(point, s.key) !== null" class="host-chart-dot is-active" :class="`is-${i + 1}`" :style="{ left: left(point.t), top: `${y(num(point, s.key) ?? 0)}%` }" aria-hidden="true" />
          </template>
          <div class="host-chart-tip" :style="tipStyle" role="status">
            <strong>{{ hourLabel(point.at) }}</strong>
            <span v-for="(s, i) in series" :key="s.key"><span class="host-chart-swatch" :class="`is-${i + 1}`" aria-hidden="true" />{{ s.label }}<b>{{ format(num(point, s.key)) }}</b></span>
            <span v-for="s in extra ?? []" :key="s.key" class="form-help">{{ s.label }}<b>{{ format(num(point, s.key)) }}</b></span>
          </div>
        </template>
      </div>
      <div class="host-chart-x" aria-hidden="true"><span v-for="(t, i) in xTicks" :key="i">{{ t }}</span></div>
    </div>
    <details class="host-chart-table" @toggle="tableOpen = ($event.target as HTMLDetailsElement).open">
      <summary>以表格查看</summary>
      <div class="metric-table-scroll" tabindex="0" :aria-label="`${title}数据表滚动区`">
        <table>
          <caption>{{ title }} · 每小时，最近的在前</caption>
          <thead><tr><th scope="col">时间</th><th v-for="s in [...series, ...(extra ?? [])]" :key="s.key" scope="col">{{ s.label }}</th></tr></thead>
          <tbody><tr v-for="r in tableRows" :key="r.at"><th scope="row"><time :datetime="r.at">{{ hourLabel(r.at) }}</time></th><td v-for="s in [...series, ...(extra ?? [])]" :key="s.key">{{ format(num(r, s.key)) }}</td></tr></tbody>
        </table>
      </div>
    </details>
  </figure>
</template>
