import { computed, toValue, type MaybeRefOrGetter } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { useOperationsAccessStore } from '../../stores/operationsAccess'
import { dataOf, isAccessError, type Schema } from '../../services/api'
export type MetricRange = Schema['MetricsOut']['range']
export type MetricViewId = Schema['MetricViewOut']['id']
export const presetViews: Record<MetricViewId, string> = { request_rate: '请求速率', latency_p50: '延迟 P50', latency_p95: '延迟 P95', latency_p99: '延迟 P99', error_rate: '5xx 占比', services_up: '服务在线' }
export function useServiceStatus(scope: string, area = 'page', enabled: MaybeRefOrGetter<boolean> = true) {
  const auth = useAuthStore(), access = useOperationsAccessStore(), user = auth.user?.id, header = { 'X-Workspace-Id': scope }
  return useQuery({ queryKey: ['operations', user, scope, area, 'services'], enabled: computed(() => toValue(enabled)), queryFn: async ({ signal }) => {
    const context = access.identity
    try { return dataOf(await auth.api.GET('/api/v1/operations/services', { params: { header }, signal })) }
    catch (cause) { if (isAccessError(cause)) access.deny(user, scope, context); throw cause }
  } })
}
export function usePresetMetrics(scope: string, range: MaybeRefOrGetter<MetricRange>, views: MaybeRefOrGetter<MetricViewId[] | undefined> = undefined, area = 'page', enabled: MaybeRefOrGetter<boolean> = true) {
  const auth = useAuthStore(), access = useOperationsAccessStore(), user = auth.user?.id, header = { 'X-Workspace-Id': scope }
  return useQuery({ queryKey: computed(() => ['operations', user, scope, area, 'metrics', toValue(range), toValue(views)]), enabled: computed(() => toValue(enabled)), queryFn: async ({ signal }) => {
    const context = access.identity
    try { return dataOf(await auth.api.GET('/api/v1/operations/metrics', { params: { header, query: { range: toValue(range), views: toValue(views) } }, signal })) }
    catch (cause) { if (isAccessError(cause)) access.deny(user, scope, context); throw cause }
  } })
}
