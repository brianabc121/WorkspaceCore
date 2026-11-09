<script setup lang="ts">
import { computed } from 'vue'
import { Clock, Layers, ShieldCheck, TriangleAlert } from '@lucide/vue'
import { aiError, type ExecutionPeriod, type ExecutionSummary } from './queries'
import { clockTime } from './approvalFormat'

// 四张汇总卡（T-0191，效果图 01 顶部）：数字只来自 GET /ai/tool-executions/summary（可见范围内 SQL 聚合），
// 不用已加载列表的长度；读不到时写“—”，不当成 0。四个数都只算所选范围内发起的调用；
// 即将到期与高危都是待审批的子集，不相加。
const props = defineProps<{ summary: ExecutionSummary | undefined; period: ExecutionPeriod; loading: boolean; error: unknown }>()
const RANGE: Record<ExecutionPeriod, string> = { '24h': '最近 24 小时发起', '7d': '最近 7 天发起', all: '不限发起时间' }
const range = computed(() => RANGE[props.period])
const value = (n: number | undefined) => (n === undefined ? '—' : String(n))
const minutes = computed(() => Math.max(1, Math.round((props.summary?.expiring_window_seconds ?? 180) / 60)))
</script>

<template>
  <section class="ag-summary ac-summary" aria-label="审批汇总" :aria-busy="loading">
    <dl class="ag-tiles">
      <div class="ag-tile ac-tile">
        <dt><ShieldCheck aria-hidden="true" /><span class="ac-tile-name">待审批</span><span class="ac-tile-range">{{ range }}</span></dt>
        <dd>{{ value(summary?.pending_count) }}</dd>
      </div>
      <div class="ag-tile ac-tile tone-amber">
        <dt><Clock aria-hidden="true" /><span class="ac-tile-name">即将到期</span><span class="ac-tile-range">{{ range }}</span></dt>
        <dd>{{ value(summary?.expiring_count) }}<small>{{ minutes }} 分钟内</small></dd>
      </div>
      <div class="ag-tile ac-tile tone-red">
        <dt><TriangleAlert aria-hidden="true" /><span class="ac-tile-name">高危待审批</span><span class="ac-tile-range">{{ range }}</span></dt>
        <dd>{{ value(summary?.critical_pending_count) }}</dd>
      </div>
      <div class="ag-tile ac-tile tone-violet">
        <dt><Layers aria-hidden="true" /><span class="ac-tile-name">执行异常</span><span class="ac-tile-range">{{ range }}</span></dt>
        <dd>{{ value(summary?.exception_count) }}<small>失败 / 结果未知</small></dd>
      </div>
    </dl>
    <p v-if="error && !summary" role="alert" class="ac-error">汇总暂时读不到：{{ aiError(error) }}</p>
    <p class="form-help">
      数字来自服务端汇总：只算所选范围内发起、符合当前智能体 / 类型 / 工具筛选的调用；即将到期与高危都包含在待审批里，不相加。<template v-if="summary">统计于 {{ clockTime(summary.as_of) }}。</template>
    </p>
  </section>
</template>
