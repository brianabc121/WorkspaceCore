<script setup lang="ts">
import { computed } from 'vue'
import { percent } from './host'

// 整机 CPU / 内存里每个智能体占多少（T-0147）：一条堆叠条，按 dataviz 规范——
// 颜色跟着智能体走（按名字的固定顺序取 --chart-1…8，不按大小重排），超过 8 个的合并成“其他智能体”（中性灰），
// 不属于任何智能体的用量（系统、Docker、别的程序）用斜纹，没用的部分留空；段之间 2px 间隔；
// 图例写出每段的数值（不只靠颜色），下面的智能体表格就是数据表。
export type Share = { key: string; label: string; value: number | null }
const props = defineProps<{ title: string; total: number | null; shares: Share[]; detail?: (value: number) => string }>()
const MAX = 8

const segments = computed(() => {
  const known = props.shares.filter(s => s.value !== null && s.value > 0) as { key: string; label: string; value: number }[]
  const colored = props.shares.slice(0, MAX).map((s, i) => ({ ...s, slot: i + 1 }))
  const rest = props.shares.slice(MAX)
  const out: { key: string; label: string; value: number; cls: string }[] = colored
    .filter(s => s.value !== null && s.value > 0)
    .map(s => ({ key: s.key, label: s.label, value: s.value as number, cls: `is-${s.slot}` }))
  const folded = rest.reduce((sum, s) => sum + (s.value ?? 0), 0)
  if (folded > 0) out.push({ key: 'other-agents', label: `其他 ${rest.length} 个智能体`, value: folded, cls: 'is-other' })
  const agents = known.reduce((sum, s) => sum + s.value, 0)
  if (props.total !== null && props.total - agents > 0.05) out.push({ key: 'system', label: '系统与其他程序', value: props.total - agents, cls: 'is-system' })
  return out
})
const legend = computed(() => [
  ...props.shares.slice(0, MAX).map((s, i) => ({ key: s.key, label: s.label, value: s.value, cls: `is-${i + 1}` })),
  ...segments.value.filter(s => s.cls === 'is-other' || s.cls === 'is-system').map(s => ({ key: s.key, label: s.label, value: s.value as number | null, cls: s.cls })),
])
const width = (value: number) => `${Math.min(100, Math.max(0, value))}%`
const text = (value: number | null) => value === null ? '无法获取' : `${percent(value)}${props.detail ? `（${props.detail(value)}）` : ''}`
const summary = computed(() => `${props.title}：整机 ${props.total === null ? '暂无数据' : percent(props.total)}。` + segments.value.map(s => `${s.label} ${percent(s.value)}`).join('，'))
</script>

<template>
  <figure class="share-bar">
    <figcaption class="host-chart-head"><strong>{{ title }}</strong><span class="form-help">整机 {{ total === null ? '—' : percent(total) }}</span></figcaption>
    <div class="share-track" role="img" :aria-label="summary">
      <span v-for="s in segments" :key="s.key" class="share-segment" :class="s.cls" :style="{ width: width(s.value) }" :title="`${s.label}：${percent(s.value)}`" />
    </div>
    <ul class="share-legend">
      <li v-for="item in legend" :key="item.key"><span class="share-swatch" :class="item.cls" aria-hidden="true" />{{ item.label }}<b>{{ text(item.value) }}</b></li>
    </ul>
  </figure>
</template>
