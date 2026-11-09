<script setup lang="ts">
import { computed } from 'vue'
import { CircleCheck, Info, TriangleAlert } from '@lucide/vue'
import type { Schema } from '../../services/api'
import { formatTime } from '../time'
import { serviceGroup, serviceKinds, serviceLabels, serviceReason, type ServiceItem } from './display'
import ServiceStateBadge from './ServiceStateBadge.vue'

// 选中服务的详情（T-0177，二批改造规范第 7.1 节）：状态、说明、最近检查都来自接口；
// 接口没有“上次成功”这类历史字段，所以不显示。Grafana 与 Prometheus 分开说：Grafana 不可用不代表指标采集失败。
const props = defineProps<{ service: ServiceItem | null; services: readonly ServiceItem[]; sync?: Schema['SyncState']; hasMetrics: boolean; refreshing: boolean }>()
const emit = defineEmits<{ refresh: [] }>()
const attention = computed(() => !!props.service && serviceGroup(props.service.status) === 'attention')
const prometheus = computed(() => props.services.find((item) => item.id === 'prometheus') ?? null)
const SYNC: Record<Schema['SyncState']['status'], string> = { ok: '正常读取', stale: '显示缓存（Prometheus 暂时读不到）', unavailable: '暂不可用', unauthorized: '无权查看', not_configured: '未部署监控' }
// 与指标有关的一行：Grafana 看 Prometheus 与指标接口；Prometheus 看指标接口
const metricsNote = computed(() => {
  const id = props.service?.id
  if (id === 'grafana') {
    if (!prometheus.value) return '没有 Prometheus 的检查结果'
    const base = `Prometheus ${serviceLabels[prometheus.value.status]}`
    return props.sync?.status === 'ok' && props.hasMetrics ? `${base}，本页的预设指标照常显示；受影响的是 Grafana 仪表盘外链` : base
  }
  if (id === 'prometheus') return props.sync ? `预设指标：${SYNC[props.sync.status]}` : '预设指标：—'
  return ''
})
</script>

<template>
  <section class="ws-panel ops-detail" aria-label="服务详情">
    <h2 class="ops-detail-title" :class="{ 'is-alert': attention }">
      <TriangleAlert v-if="attention" aria-hidden="true" /><CircleCheck v-else-if="service" aria-hidden="true" /><Info v-else aria-hidden="true" />
      {{ attention ? '异常详情' : '服务详情' }}
    </h2>
    <p v-if="!service" class="ops-muted">当前没有降级或不可用的服务。点选表格里的服务查看检查详情。</p>
    <template v-else>
      <dl class="ws-facts ops-facts">
        <dt>服务</dt><dd>{{ service.name }}</dd>
        <dt>类型</dt><dd>{{ serviceKinds[service.kind] }}</dd>
        <dt>状态</dt><dd><ServiceStateBadge :status="service.status" /></dd>
        <dt>说明</dt><dd>{{ serviceReason(service) || '—' }}</dd>
        <dt>最近检查</dt><dd><time v-if="service.checked_at" :datetime="service.checked_at">{{ formatTime(service.checked_at) }}</time><span v-else>暂无记录</span></dd>
        <template v-if="service.latency_ms !== undefined && service.latency_ms !== null"><dt>检查耗时</dt><dd>{{ service.latency_ms }} ms</dd></template>
        <template v-if="metricsNote"><dt>指标采集</dt><dd>{{ metricsNote }}</dd></template>
      </dl>
      <button type="button" class="secondary-button ops-detail-action" :disabled="refreshing" @click="emit('refresh')">{{ refreshing ? '正在读取…' : '重新读取检查结果' }}</button>
    </template>
  </section>
</template>
