<script setup lang="ts">
import { computed, ref } from 'vue'
import { useAuthStore } from '../../stores/auth'
import { useOperationsAccessStore } from '../../stores/operationsAccess'
import { isAccessError } from '../../services/api'
import { useServiceStatus, usePresetMetrics, presetViews, type MetricRange, type MetricViewId } from './queries'
import { safeUrl } from '../../widgets/registry'
import ServiceList from './ServiceList.vue'
import MetricView from './MetricView.vue'
import OperationsNotice from './OperationsNotice.vue'
import ExternalLink from '../../components/ExternalLink.vue'
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id
const access = useOperationsAccessStore(), range = ref<MetricRange>('1h'), selectedView = ref<MetricViewId | ''>('')
const services = useServiceStatus(scope, 'page', () => !access.denied), metrics = usePresetMetrics(scope, range, () => selectedView.value ? [selectedView.value] : undefined, 'page', () => !access.denied)
const denied = computed(() => access.denied || isAccessError(services.error.value) || isAccessError(metrics.error.value))
const items = computed(() => denied.value ? [] : services.data.value?.items ?? [])
const metricDenied = computed(() => denied.value || metrics.data.value?.sync.status === 'unauthorized')
const views = computed(() => metricDenied.value || ['unavailable', 'not_configured'].includes(metrics.data.value?.sync.status ?? '') ? [] : metrics.data.value?.views ?? [])
const grafana = computed(() => !metricDenied.value && metrics.data.value?.grafana_url && safeUrl(metrics.data.value.grafana_url) ? metrics.data.value.grafana_url : null)
async function refresh() {
  if (access.denied && !await access.recover(user, scope)) return
  if (auth.user?.id === user && auth.workspaceId === scope) await Promise.all([services.refetch(), metrics.refetch()])
}
</script>
<template><div class="task-toolbar"><p class="form-help">只读状态与预设指标。复杂查询和告警配置请使用 Grafana。</p><button class="secondary-button" :disabled="access.checking || services.isFetching.value || metrics.isFetching.value" @click="refresh">刷新运维数据</button></div><section class="panel form-panel" aria-label="服务状态"><h2>服务状态</h2><OperationsNotice subject="services" :loading="services.isPending.value && !denied" :error="services.error.value" :denied="denied" :as-of="services.data.value?.generated_at" :empty="!items.length" /><ServiceList :items="items" /></section><section class="panel form-panel" aria-label="预设指标"><div class="section-heading"><h2>预设指标</h2><ExternalLink v-if="grafana" :href="grafana" label="在 Grafana 中查看" /></div><div class="task-filters"><label>查看时间范围<select v-model="range" aria-label="查看时间范围"><option value="1h">最近 1 小时</option><option value="6h">最近 6 小时</option><option value="24h">最近 24 小时</option><option value="7d">最近 7 天</option></select></label><label>预设指标视图<select v-model="selectedView" aria-label="预设指标视图"><option value="">全部预设视图</option><option v-for="(title, id) in presetViews" :key="id" :value="id">{{ title }}</option></select></label></div><OperationsNotice subject="metrics" :loading="metrics.isPending.value && !metricDenied" :error="metrics.error.value" :denied="metricDenied" :sync="metrics.data.value?.sync" :empty="!views.length" /><div class="metrics-grid"><MetricView v-for="view in views" :key="view.id" :view="view" /></div></section></template>
