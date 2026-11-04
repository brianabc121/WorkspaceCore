<script setup lang="ts">
import { computed, ref } from 'vue'

// 近 8 周的柱状图（T-0100，知识库主页与智能体主页的趋势）。
// 按 dataviz 规范：一条轴；系列颜色固定顺序（--kb-chart-1 蓝、--kb-chart-2 橙，亮暗两套都跑过色觉检查）；
// 叠放的段之间留 2px 底色缝，柱顶 4px 圆角；两个以上系列有图例；悬停 / 键盘聚焦一周显示数值；
// 数字和文字只用文字色；“以表格查看”给读屏和不看颜色的人。
export type ChartSeries = { key: string; label: string }
export type ChartWeek = { week_start: string; values: Record<string, number> }
const props = defineProps<{ title: string; note?: string; series: ChartSeries[]; weeks: ChartWeek[]; empty: string }>()

const total = (w: ChartWeek) => props.series.reduce((sum, s) => sum + (w.values[s.key] ?? 0), 0)
// 轴的最大值取 1、2、5 × 10ⁿ 里不小于最大值的那个，刻度 0、一半、最大
const top = computed(() => {
  const max = Math.max(0, ...props.weeks.map(total))
  if (max <= 4) return 4
  const p = 10 ** Math.floor(Math.log10(max))
  return ([1, 2, 5, 10].map(f => f * p).find(v => v >= max) ?? 10 * p)
})
const ticks = computed(() => [top.value, top.value / 2, 0])
const nothing = computed(() => props.weeks.every(w => total(w) === 0))
const label = (week: string) => { const [, m, d] = week.split('-').map(Number); return `${m}/${d}` }
const longLabel = (week: string) => { const [, m, d] = week.split('-').map(Number); return `${m} 月 ${d} 日起的一周` }
const describe = (w: ChartWeek) => `${longLabel(w.week_start)}：` + props.series.map(s => `${s.label} ${w.values[s.key] ?? 0} 次`).join('，')
const summary = computed(() => {
  const last = props.weeks.at(-1)
  return `${props.title}，近 ${props.weeks.length} 周。` + (last ? `最近一周 ${props.series.map(s => `${s.label} ${last.values[s.key] ?? 0} 次`).join('，')}。` : '')
})
const active = ref<number | null>(null)
const tipStyle = computed(() => {
  const i = active.value
  if (i === null) return {}
  // 放在这一周的旁边（左半边放右侧，右半边放左侧），不挡住柱子和图例
  const n = props.weeks.length
  return i < n / 2 ? { left: `calc(${((i + 1) / n) * 100}% + 4px)` } : { left: `calc(${(i / n) * 100}% - 4px)`, transform: 'translateX(-100%)' }
})
</script>

<template>
  <figure class="kb-chart">
    <figcaption class="kb-chart-head">
      <span class="kb-stack"><strong>{{ title }}</strong><span v-if="note" class="kb-meta">{{ note }}</span></span>
      <span v-if="series.length > 1" class="kb-chart-legend">
        <span v-for="(s, i) in series" :key="s.key"><span class="kb-chart-swatch" :class="`is-${i + 1}`" aria-hidden="true" />{{ s.label }}</span>
      </span>
    </figcaption>
    <p v-if="nothing" class="kb-meta kb-chart-empty">{{ empty }}</p>
    <div v-else class="kb-chart-body">
      <div class="kb-chart-y" aria-hidden="true"><span v-for="t in ticks" :key="t" class="kb-num">{{ t }}</span></div>
      <div class="kb-chart-plot">
        <div class="kb-chart-grid" aria-hidden="true"><span v-for="t in ticks" :key="t" /></div>
        <ul class="kb-chart-cols" :aria-label="summary" @mouseleave="active = null">
          <li v-for="(w, i) in weeks" :key="w.week_start" class="kb-chart-col" tabindex="0" :aria-label="describe(w)" @mouseenter="active = i" @focus="active = i" @blur="active = null">
            <span class="kb-chart-bar" :style="{ height: `${(total(w) / top) * 100}%` }">
              <span v-for="(s, k) in series" v-show="(w.values[s.key] ?? 0) > 0" :key="s.key" class="kb-chart-seg" :class="`is-${k + 1}`" :style="{ flexGrow: w.values[s.key] ?? 0 }" />
            </span>
          </li>
        </ul>
        <div v-if="active !== null && weeks[active]" class="kb-chart-tip" :style="tipStyle" role="status">
          <strong>{{ longLabel(weeks[active].week_start) }}</strong>
          <span v-for="(s, k) in series" :key="s.key"><span class="kb-chart-swatch" :class="`is-${k + 1}`" aria-hidden="true" />{{ s.label }}<b class="kb-num">{{ weeks[active].values[s.key] ?? 0 }}</b></span>
          <span v-if="series.length > 1" class="kb-meta">合计<b class="kb-num">{{ total(weeks[active]) }}</b></span>
        </div>
      </div>
      <div class="kb-chart-x" aria-hidden="true"><span v-for="w in weeks" :key="w.week_start" class="kb-num">{{ label(w.week_start) }}</span></div>
    </div>
    <details class="kb-chart-table">
      <summary>以表格查看</summary>
      <table>
        <thead><tr><th scope="col">周（起始日）</th><th v-for="s in series" :key="s.key" scope="col">{{ s.label }}</th></tr></thead>
        <tbody><tr v-for="w in weeks" :key="w.week_start"><th scope="row">{{ longLabel(w.week_start) }}</th><td v-for="s in series" :key="s.key" class="kb-num">{{ w.values[s.key] ?? 0 }}</td></tr></tbody>
      </table>
    </details>
  </figure>
</template>
