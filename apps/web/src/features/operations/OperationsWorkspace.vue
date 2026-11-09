<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useQueryClient } from '@tanstack/vue-query'
import { RefreshCw, TriangleAlert } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { useOperationsAccessStore } from '../../stores/operationsAccess'
import { isAccessError } from '../../services/api'
import { useServiceStatus, usePresetMetrics, presetViews, type MetricRange, type MetricViewId } from './queries'
import { safeUrl } from '../../widgets/registry'
import { serviceGroup, serviceGroups, sortServices } from './display'
import ServiceHealthSummary from './ServiceHealthSummary.vue'
import ServiceStatusTable from './ServiceStatusTable.vue'
import ServiceDetailPanel from './ServiceDetailPanel.vue'
import MetricSeriesChart from './MetricSeriesChart.vue'
import OperationsNotice from './OperationsNotice.vue'
import StoragePanel from './StoragePanel.vue'
import '../workspace/workspace-pages.css'
import './operations.css'

// 运维页（T-0177，二批改造规范第 7 节，效果图 04）：健康摘要 → 异常提示 → 预设指标 → 服务状态表 + 选中服务详情 + 存储状态。
// 只读：普通刷新只做 GET（服务检查结果、预设指标、存储状态），不重启服务、不跑命令、不执行存储读写自检。
// 失权（403）由 useOperationsAccessStore 统一处理：清掉已显示的服务、指标与 Grafana 入口。
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id
const cache = useQueryClient()
const canManage = computed(() => ['owner', 'admin'].includes(auth.currentWorkspace?.role ?? ''))
const access = useOperationsAccessStore(), range = ref<MetricRange>('1h'), selectedView = ref<MetricViewId | ''>('')
const viewIds = () => (selectedView.value ? [selectedView.value] : undefined)
const services = useServiceStatus(scope, 'page', () => !access.denied), metrics = usePresetMetrics(scope, range, viewIds, 'page', () => !access.denied)
const denied = computed(() => access.denied || isAccessError(services.error.value) || isAccessError(metrics.error.value))
const loaded = computed(() => (denied.value ? null : services.data.value?.items ?? null))
const items = computed(() => loaded.value ?? [])
const sorted = computed(() => sortServices(items.value))
const counts = computed(() => serviceGroups(items.value))
const metricDenied = computed(() => denied.value || metrics.data.value?.sync.status === 'unauthorized')
// 只显示与当前所选范围一致的响应：切换范围时旧范围的结果不会顶替新范围
const current = computed(() => (metrics.data.value?.range === range.value ? metrics.data.value : undefined))
const views = computed(() => metricDenied.value || ['unavailable', 'not_configured'].includes(current.value?.sync.status ?? '') ? [] : current.value?.views ?? [])
const grafana = computed(() => !metricDenied.value && current.value?.grafana_url && safeUrl(current.value.grafana_url) ? current.value.grafana_url : null)
const RANGES: { value: MetricRange; label: string }[] = [{ value: '1h', label: '1 小时' }, { value: '6h', label: '6 小时' }, { value: '24h', label: '24 小时' }, { value: '7d', label: '7 天' }]

// 切换范围或视图：取消还在路上的旧请求（它的结果也不会显示在新范围下）
watch([range, selectedView], (_, [oldRange, oldView]) => {
  void cache.cancelQueries({ queryKey: ['operations', user, scope, 'page', 'metrics', oldRange, oldView ? [oldView] : undefined], exact: true })
})

// 选中的服务：默认第一个需要关注的；选中的服务在刷新后消失时回到默认
const selectedId = ref<string | null>(null)
const selected = computed(() => sorted.value.find((item) => item.id === selectedId.value) ?? sorted.value.find((item) => serviceGroup(item.status) === 'attention') ?? null)
const attention = computed(() => sorted.value.filter((item) => serviceGroup(item.status) === 'attention'))
const grafanaOnly = computed(() => {
  const prometheus = items.value.find((item) => item.id === 'prometheus')
  return attention.value.some((item) => item.id === 'grafana') && prometheus?.status === 'ok' && current.value?.sync.status === 'ok'
})

const refreshing = computed(() => access.checking || services.isFetching.value || metrics.isFetching.value)
async function refresh() {
  if (access.denied && !await access.recover(user, scope)) return
  if (auth.user?.id !== user || auth.workspaceId !== scope) return
  await Promise.all([
    services.refetch(), metrics.refetch(),
    // 存储状态也只是重新读取（GET），不会执行读写自检
    canManage.value ? cache.invalidateQueries({ queryKey: ['operations', user, scope, 'storage'], exact: true }) : undefined,
  ])
}
async function refreshServices() { if (!access.denied) await services.refetch() }
</script>

<template>
  <header class="ws-header">
    <div>
      <h1>运维</h1>
      <p>服务健康、关键指标与存储状态。只读检查；复杂查询与告警配置请使用 Grafana。</p>
    </div>
    <div class="ws-actions">
      <button type="button" class="secondary-button" :disabled="refreshing" @click="refresh"><RefreshCw aria-hidden="true" />刷新运维数据</button>
      <a v-if="grafana" class="secondary-button" :href="grafana" target="_blank" rel="noopener noreferrer">在 Grafana 中查看 ↗</a>
    </div>
  </header>

  <ServiceHealthSummary :items="loaded" :stale="!!services.error.value && !!services.data.value" />

  <div v-if="attention.length" class="ops-alert" role="status">
    <TriangleAlert aria-hidden="true" />
    <p>
      <strong>{{ attention.length }} 个服务需要关注</strong>：{{ attention.map((item) => item.name).join('、') }}。<template v-if="grafanaOnly">Prometheus 采集正常，预设指标仍可查看。</template>
    </p>
    <button v-if="attention[0]!.id !== selected?.id" type="button" class="secondary-button" @click="selectedId = attention[0]!.id">查看详情</button>
  </div>

  <section class="ops-metrics" aria-label="预设指标">
    <div class="ops-toolbar">
      <div class="ops-toolbar-group">
        <span class="ops-toolbar-label" aria-hidden="true">时间范围</span>
        <div class="ws-segment" role="group" aria-label="查看时间范围">
          <button v-for="item in RANGES" :key="item.value" type="button" :aria-pressed="range === item.value" @click="range = item.value">{{ item.label }}</button>
        </div>
      </div>
      <label class="ops-toolbar-group">
        <span class="ops-toolbar-label">预设视图</span>
        <select v-model="selectedView" aria-label="预设指标视图"><option value="">全部预设视图</option><option v-for="(title, id) in presetViews" :key="id" :value="id">{{ title }}</option></select>
      </label>
    </div>
    <OperationsNotice subject="metrics" :loading="metrics.isPending.value && !metricDenied" :error="metrics.error.value" :denied="metricDenied" :sync="current?.sync" :empty="!views.length" />
    <div v-if="views.length" class="ops-metric-grid">
      <MetricSeriesChart v-for="view in views" :key="view.id" :view="view" :range="current!.range" :step-seconds="current!.step_seconds" />
    </div>
  </section>

  <div class="ops-split">
    <section class="ws-panel ops-services" aria-label="服务状态">
      <div class="ops-panel-head">
        <h2>服务状态</h2>
        <span v-if="loaded" class="ops-counts">
          <span class="ops-count is-ok">正常 {{ counts.ok }}</span>
          <span class="ops-count is-attention">需关注 {{ counts.attention }}</span>
          <span v-if="counts.unknown" class="ops-count">未检查 {{ counts.unknown }}</span>
          <span v-if="counts.not_configured" class="ops-count">未配置 {{ counts.not_configured }}</span>
        </span>
      </div>
      <OperationsNotice subject="services" :loading="services.isPending.value && !denied" :error="services.error.value" :denied="denied" :as-of="services.data.value?.generated_at" :empty="!items.length" />
      <ServiceStatusTable v-if="sorted.length" :items="sorted" :selected="selected?.id ?? null" @select="selectedId = $event" />
    </section>
    <div class="ops-side">
      <ServiceDetailPanel v-if="sorted.length" :service="selected" :services="items" :sync="current?.sync" :has-metrics="views.length > 0" :refreshing="services.isFetching.value" @refresh="refreshServices" />
      <StoragePanel v-if="canManage && !denied" />
    </div>
  </div>
</template>
