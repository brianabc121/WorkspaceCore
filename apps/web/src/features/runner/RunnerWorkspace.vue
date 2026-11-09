<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute, useRouter, type LocationQuery } from 'vue-router'
import { useQueryClient } from '@tanstack/vue-query'
import { Box, CirclePlay, CircleStop, RefreshCw, ShieldCheck, TriangleAlert } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { useMediaQuery } from '../../composables/useMediaQuery'
import DrawerDetail from '../../components/DrawerDetail.vue'
import { runnerError, useRunnerOverview, type EnvFilters, type EnvSort, type RunnerEnv } from './api'
import { summary } from './containerMetrics'
import ContainerHostStrip from './ContainerHostStrip.vue'
import ContainerDetailPanel from './ContainerDetailPanel.vue'
import OverviewTab from './OverviewTab.vue'
import AgentUsageTab from './AgentUsageTab.vue'
import TemplatesTab from './TemplatesTab.vue'
import RunsTab from './RunsTab.vue'
import SettingsTab from './SettingsTab.vue'

// 容器服务器页（原“运行服务器”，T-0140；改版 T-0186 / T-0187，效果图 docs/v2/container-server-redesign/assets/01）：
// 页头、主机条、四个统计卡（overview 的全量，范围是本工作空间未删除的环境记录）、五个标签。
// 当前标签、筛选与选中的容器写在地址里（?tab= / ?instance_id= / ?lifetime= / ?state= / ?q= / ?sort= / ?environment_id=），
// 刷新、通知链接、后退都能回到同一处；旧链接 ?tab=environments 换成容器总览并保留其余参数。
// 宽屏（1440 以上）详情在右侧一栏，窄屏放进右侧抽屉；关掉后焦点回到打开它的那一行。
const TABS = [
  { key: 'overview', name: '容器总览' },
  { key: 'instances', name: '智能体用量' },
  { key: 'templates', name: '环境模板' },
  { key: 'runs', name: '执行记录' },
  { key: 'settings', name: '服务器设置' },
] as const
type Tab = typeof TABS[number]['key']
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id
const route = useRoute(), router = useRouter(), cache = useQueryClient()
const wide = typeof window.matchMedia === 'function' ? useMediaQuery('(min-width: 1440px)') : ref(false)

const one = (value: unknown) => (typeof value === 'string' && value ? value : undefined)
const pick = (value: unknown): Tab => (TABS.some(t => t.key === value) ? value as Tab : 'overview')
const tab = computed<Tab>(() => pick(route.query.tab))
// 旧链接 ?tab=environments（原来的“环境”标签、旧通知）：换成容器总览，其余参数保留
if (route.query.tab === 'environments') {
  const rest: LocationQuery = { ...route.query }
  delete rest.tab
  void router.replace({ query: rest })
}
const STATES = ['creating', 'running', 'stopped', 'archived', 'pending_delete', 'deleted', 'failed']
const filters = computed<EnvFilters>(() => {
  const q = route.query
  const state = one(q.state), lifetime = one(q.lifetime), sort = one(q.sort)
  return {
    instance_id: one(q.instance_id),
    lifetime: lifetime && ['once', 'session', 'persistent'].includes(lifetime) ? lifetime as RunnerEnv['lifetime'] : undefined,
    state: state && STATES.includes(state) ? state as RunnerEnv['state'] : undefined,
    q: one(q.q)?.slice(0, 100),
    sort: sort === 'cpu' || sort === 'memory' ? sort as EnvSort : undefined,
    include_deleted: q.include_deleted === '1' ? true : undefined,
  }
})
const selected = computed(() => one(route.query.environment_id) ?? null)

function update(patch: Record<string, string | undefined>, push = false) {
  const query: LocationQuery = { ...route.query }
  for (const [k, v] of Object.entries(patch)) { if (v === undefined || v === '') delete query[k]; else query[k] = v }
  void (push ? router.push({ query }) : router.replace({ query }))
}
function go(next: Tab) { update({ tab: next === 'overview' ? undefined : next }) }
function setFilters(next: EnvFilters) {
  update({ instance_id: next.instance_id, lifetime: next.lifetime, state: next.state, q: next.q, sort: next.sort === 'last_used' ? undefined : next.sort, include_deleted: next.include_deleted ? '1' : undefined })
}
let opener: HTMLElement | null = null
function select(id: string, row: HTMLElement | null) { opener = row; update({ environment_id: id }, true) }
async function close() {
  update({ environment_id: undefined })
  const back = opener
  opener = null
  await nextTick()
  if (back?.isConnected) back.focus()
  else document.querySelector<HTMLElement>(`tr[data-env="${CSS.escape(selected.value ?? '')}"]`)?.focus()
}
function agentContainers(instanceId: string) { update({ tab: undefined, instance_id: instanceId, state: undefined, lifetime: undefined, q: undefined }) }
const runsFor = ref<string | undefined>()
function showRuns(environmentId: string) { runsFor.value = environmentId; update({ tab: 'runs', environment_id: undefined }) }
watch(tab, (t) => { if (t !== 'runs') runsFor.value = undefined })

const overview = useRunnerOverview(scope)
const data = computed(() => overview.data.value)
const instances = computed(() => data.value?.instances ?? [])
const counts = computed(() => summary(data.value?.environments))
const reload = ref(0)
const refreshing = computed(() => overview.isFetching.value)
async function refresh() {
  reload.value += 1
  await cache.invalidateQueries({ predicate: q => q.queryKey[0] === 'runner' && q.queryKey[1] === user && q.queryKey[2] === scope })
}
const value = (n: number | null | undefined) => (n === null || n === undefined ? '—' : String(n))
const showDetail = computed(() => tab.value === 'overview' && !!selected.value)
</script>

<template>
  <header class="ws-header">
    <div>
      <h1>容器服务器</h1>
      <p>统一查看 OpenClaw 申请的容器、资源负载与生命周期（owner 与 admin）。</p>
    </div>
    <div class="ws-actions">
      <button type="button" class="secondary-button" :disabled="refreshing" @click="refresh"><RefreshCw aria-hidden="true" />刷新</button>
      <RouterLink class="primary-button" to="/agents" title="在 OpenClaw 智能体页的实例详情里打开或关闭“运行环境权限”、调整限额"><ShieldCheck aria-hidden="true" />管理申请权限</RouterLink>
    </div>
  </header>

  <p v-if="overview.isPending.value" role="status">正在加载…</p>
  <div v-else-if="overview.error.value && !data" class="ws-state-line"><p role="alert">{{ runnerError(overview.error.value) }}</p><button type="button" class="secondary-button" @click="overview.refetch()">重试</button></div>
  <template v-else-if="data">
    <ContainerHostStrip v-if="data.host" :host="data.host" @settings="go('settings')" />
    <section v-else class="ws-panel cs-empty" aria-label="还没有容器服务器">
      <p>还没有接入容器服务器。智能体要在容器里执行命令，先在“服务器设置”里添加一台（专用的 Linux 服务器，装 Docker 与 gVisor）。</p>
      <button type="button" class="primary-button" @click="go('settings')">去添加容器服务器</button>
    </section>

    <section class="ag-summary" aria-label="容器统计">
      <dl class="ag-tiles">
        <div class="ag-tile"><dt><Box aria-hidden="true" />容器环境总数</dt><dd>{{ value(counts?.total) }}</dd></div>
        <div class="ag-tile tone-blue cs-tile-run"><dt><CirclePlay aria-hidden="true" />运行中</dt><dd>{{ value(counts?.running) }}</dd></div>
        <div class="ag-tile tone-muted"><dt><CircleStop aria-hidden="true" />已停止</dt><dd>{{ value(counts?.stopped) }}</dd></div>
        <div class="ag-tile tone-amber"><dt><TriangleAlert aria-hidden="true" />需关注</dt><dd>{{ value(counts?.attention) }}<button v-if="counts?.attention" type="button" class="asv-link cs-tile-link" @click="setFilters({ ...filters, state: 'failed' }); go('overview')">查看</button></dd></div>
      </dl>
      <p class="form-help">本工作空间未删除的环境记录（含已归档与创建中，不是此刻 Docker 里的容器数）；需关注 = 失败的环境。</p>
    </section>

    <div class="cs-tabbar" role="tablist" aria-label="容器服务器">
      <button v-for="t in TABS" :key="t.key" type="button" role="tab" :aria-selected="tab === t.key" @click="go(t.key)">{{ t.name }}</button>
    </div>

    <div class="asv-layout" :class="{ 'has-side': showDetail && wide }">
      <div class="asv-main" role="tabpanel" :aria-label="TABS.find(t => t.key === tab)?.name">
        <OverviewTab v-if="tab === 'overview'" :instances="instances" :filters="filters" :workspace-total="counts?.total ?? null" :selected-id="selected" :recent="data.recent" :reload="reload" @filters="setFilters" @select="select" />
        <AgentUsageTab v-else-if="tab === 'instances'" :instances="instances" @containers="agentContainers" />
        <TemplatesTab v-else-if="tab === 'templates'" />
        <RunsTab v-else-if="tab === 'runs'" :instances="instances" :environment-id="runsFor" @clear="runsFor = undefined" />
        <SettingsTab v-else :host="data.host" />
      </div>
      <aside v-if="showDetail && wide" class="ws-panel asv-side cs-side" aria-label="容器详情">
        <ContainerDetailPanel :key="selected!" :environment-id="selected!" aside @close="close" @runs="showRuns" />
      </aside>
    </div>
    <DrawerDetail v-if="showDetail && !wide" title="容器详情" @close="close">
      <ContainerDetailPanel :key="selected!" :environment-id="selected!" @close="close" @runs="showRuns" />
    </DrawerDetail>
  </template>
</template>
