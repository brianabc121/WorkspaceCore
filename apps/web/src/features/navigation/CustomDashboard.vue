<script setup lang="ts">
import { computed } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { dataOf } from '../../services/api'
import { useMediaQuery } from '../../composables/useMediaQuery'
import DataState from '../../components/DataState.vue'
import DesktopGrid from '../../widgets/DesktopGrid.vue'
import WidgetFrame from '../../widgets/WidgetFrame.vue'

// 侧栏里的“仪表盘”页面（T-0161）：只读显示一个仪表盘的组件。添加、调整组件仍在总览里选这个仪表盘编辑，
// 免得同一个仪表盘有两处编辑入口。查询键与总览相同（overview / widgets），实时事件会一起刷新。
const props = defineProps<{ dashboardId: string }>()
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id
const header = { 'X-Workspace-Id': scope }
const list = useQuery({ queryKey: ['dashboards', user, scope], queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/dashboards', { params: { header }, signal })) })
const overview = useQuery({ queryKey: ['overview', user, scope], queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/dashboard/overview', { params: { header }, signal })) })
const widgets = useQuery({
  queryKey: computed(() => ['widgets', user, scope, props.dashboardId]),
  queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/dashboards/{dashboard_id}/widgets', { params: { header, path: { dashboard_id: props.dashboardId } }, signal })),
})
const dashboard = computed(() => list.data.value?.items.find((item) => item.id === props.dashboardId) ?? null)
const items = computed(() => widgets.data.value?.items ?? [])
const mobileItems = computed(() => [...items.value].sort((a, b) => a.layout.mobile.y - b.layout.mobile.y || a.id.localeCompare(b.id)))
const cardLoading = computed(() => overview.isFetching.value && !overview.data.value)
const wide = useMediaQuery('(min-width: 761px)')
</script>

<template>
  <section aria-label="仪表盘">
    <p class="form-help">只读显示{{ dashboard ? `仪表盘「${dashboard.title}」` : '这个仪表盘' }}。要添加或调整组件，到总览的“选择仪表盘”里打开它编辑。</p>
    <DataState :loading="widgets.isPending.value" :error="widgets.error.value" :empty="!items.length" empty-text="这个仪表盘还没有组件。" :stale="!!items.length" @retry="widgets.refetch()" />
    <template v-if="items.length">
      <DesktopGrid v-if="wide" :key="dashboardId + items.map((w) => w.id).join()" :widgets="items" :editing="false">
        <template #default="{ widget: item }"><WidgetFrame :widget="item" :overview="overview.data.value" :loading="cardLoading" :failed="!!overview.error.value" @retry="overview.refetch()" /></template>
      </DesktopGrid>
      <div v-else class="mobile-widget-grid">
        <WidgetFrame v-for="item in mobileItems" :key="item.id" :widget="item" :overview="overview.data.value" :loading="cardLoading" :failed="!!overview.error.value" :style="{ minHeight: `${item.layout.mobile.h * 64}px` }" @retry="overview.refetch()" />
      </div>
    </template>
  </section>
</template>
