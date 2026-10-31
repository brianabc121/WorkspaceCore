<script setup lang="ts">
import { computed } from 'vue'
import { useQueryClient } from '@tanstack/vue-query'
import { isAccessError, type Schema } from '../services/api'
import { useAuthStore } from '../stores/auth'
import { useOperationsAccessStore } from '../stores/operationsAccess'
import type { WidgetValue } from './registry'
import { useServiceStatus, usePresetMetrics } from '../features/operations/queries'
import ServiceList from '../features/operations/ServiceList.vue'
import MetricView from '../features/operations/MetricView.vue'
import OperationsNotice from '../features/operations/OperationsNotice.vue'
const props = defineProps<{ value: WidgetValue; overview?: Schema['DashboardOverview']; loading?: boolean; failed?: boolean }>()
const emit = defineEmits<{ retry: [] }>(), auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id
const access = useOperationsAccessStore(), cache = useQueryClient()
const health = computed(() => props.value.widget_type === 'service_health'), limit = computed(() => props.value.widget_type === 'service_health' ? props.value.config.limit : 8)
const latency = computed(() => props.value.widget_type === 'api_latency' ? props.value.config : { range: '1h' as const, quantile: 'p95' as const })
const module = computed(() => props.overview?.modules.operations), canRead = computed(() => !!module.value && module.value.status !== 'unauthorized')
const services = useServiceStatus(scope, 'widget', () => canRead.value && health.value)
const metrics = usePresetMetrics(scope, () => latency.value.range, () => [('latency_' + latency.value.quantile) as Schema['MetricViewOut']['id']], 'widget', () => canRead.value && !health.value)
const error = computed(() => health.value ? services.error.value : metrics.error.value)
const denied = computed(() => access.denied || module.value?.status === 'unauthorized' || isAccessError(error.value) || !health.value && metrics.data.value?.sync.status === 'unauthorized')
const items = computed(() => denied.value ? [] : services.data.value?.items.slice(0, limit.value) ?? [])
const views = computed(() => denied.value || ['unavailable', 'not_configured'].includes(metrics.data.value?.sync.status ?? '') ? [] : metrics.data.value?.views.filter((view) => view.id === 'latency_' + latency.value.quantile) ?? [])
async function recover() {
  if (await access.recover(user, scope)) {
    await cache.invalidateQueries({ predicate: (query) => query.queryKey[0] === 'operations' && query.queryKey[1] === user && query.queryKey[2] === scope })
    emit('retry')
  }
}
function retry() { if (canRead.value) void (health.value ? services.refetch() : metrics.refetch()); emit('retry') }
</script>
<template><p v-if="module?.status === 'unavailable'" class="feedback">{{ module.reason === 'service_down' ? '有核心服务不可用' : '运维模块暂不可用' }}</p><p v-if="!module && !loading" class="feedback">运维模块暂不可用。</p><OperationsNotice v-if="module || loading" :subject="health ? 'services' : 'metrics'" :loading="!!loading || canRead && (health ? services.isPending.value : metrics.isPending.value)" :error="error" :denied="denied" :sync="health ? undefined : metrics.data.value?.sync" :as-of="health ? services.data.value?.generated_at : undefined" :stale="!!failed || module?.status === 'stale'" :empty="health ? !items.length : !views.length" /><button v-if="access.denied" class="secondary-button" :disabled="access.checking" @click="recover">重新验证运维权限</button><ServiceList v-if="health && !denied" :items="items" compact /><template v-else-if="!health && !denied"><p class="form-help">{{ latency.quantile.toUpperCase() }} · {{ latency.range }}</p><MetricView v-for="view in views" :key="view.id" :view="view" compact /></template><RouterLink v-if="!denied" to="/operations">查看运维页</RouterLink><button v-if="!denied && (error || failed || module?.retryable)" class="secondary-button" :disabled="services.isFetching.value || metrics.isFetching.value" @click="retry">刷新运维组件</button></template>
