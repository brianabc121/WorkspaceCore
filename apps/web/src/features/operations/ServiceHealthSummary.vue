<script setup lang="ts">
import { computed } from 'vue'
import { CircleCheck, CircleDashed, CircleMinus, Clock, Server, TriangleAlert } from '@lucide/vue'
import { latestCheck, serviceGroups, type ServiceItem } from './display'

// 运维页顶部的健康摘要（T-0177，二批改造规范第 7.1 节）：按接口返回的完整服务数组分组计数，不写死服务数量。
// 正常 = ok；需关注 = degraded / down；未检查 = unknown；未配置 = not_configured（停用的组件、没配置的连接不算故障）。
// 最近检查取各服务的 checked_at 里最新的一次，显示具体时间；没有数据时一律“—”，不写 0 也不写“刚刚”。
const props = defineProps<{ items: readonly ServiceItem[] | null; stale?: boolean }>()
const counts = computed(() => (props.items ? serviceGroups(props.items) : null))
const latest = computed(() => (props.items ? latestCheck(props.items) : null))
const clock = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
const day = new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' })
const value = (n: number | undefined) => (n === undefined ? '—' : String(n))
</script>

<template>
  <section class="ops-summary" aria-label="服务健康摘要">
    <dl class="ops-tiles">
      <div class="ops-tile tone-blue"><dt><Server aria-hidden="true" />服务</dt><dd>{{ value(items?.length) }}</dd></div>
      <div class="ops-tile tone-green"><dt><CircleCheck aria-hidden="true" />正常</dt><dd>{{ value(counts?.ok) }}</dd></div>
      <div class="ops-tile tone-amber" :class="{ 'is-alert': !!counts?.attention }"><dt><TriangleAlert aria-hidden="true" />需关注</dt><dd>{{ value(counts?.attention) }}</dd></div>
      <div class="ops-tile tone-muted"><dt><CircleDashed aria-hidden="true" />未检查</dt><dd>{{ value(counts?.unknown) }}</dd></div>
      <div class="ops-tile tone-muted"><dt><CircleMinus aria-hidden="true" />未配置</dt><dd>{{ value(counts?.not_configured) }}</dd></div>
      <div class="ops-tile tone-blue is-time">
        <dt><Clock aria-hidden="true" />最近检查</dt>
        <dd>
          <time v-if="latest" :datetime="latest"><strong>{{ clock.format(new Date(latest)) }}</strong><small>{{ day.format(new Date(latest)) }}{{ stale ? ' · 缓存' : '' }}</small></time>
          <template v-else>—</template>
        </dd>
      </div>
    </dl>
  </section>
</template>
