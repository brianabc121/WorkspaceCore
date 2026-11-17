<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute, useRouter, type LocationQuery } from 'vue-router'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { Box, CircleCheck, CirclePlay, Plus, RefreshCw, Search, Server as ServerIcon, X } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { useMediaQuery } from '../../composables/useMediaQuery'
import DrawerDetail from '../../components/DrawerDetail.vue'
import { dataOf } from '../../services/api'
import { HOST_STATUS, runnerError, useRunnerOverview, type EnvFilters, type EnvSort, type RunnerEnv, type RunnerHost } from './api'
import { ago, summary } from './containerMetrics'
import { HOST_TONE, INGRESS, hostAlerts, hostMetrics, hostResources } from './hosts'
import RunnerServerDetail from './RunnerServerDetail.vue'
import RunnerAddForm from './RunnerAddForm.vue'
import ContainerDetailPanel from './ContainerDetailPanel.vue'
import OverviewTab from './OverviewTab.vue'
import AgentUsageTab from './AgentUsageTab.vue'
import TemplatesTab from './TemplatesTab.vue'
import RunsTab from './RunsTab.vue'

// 容器服务器页（原“运行服务器”，T-0140；改版 T-0186 / T-0187；T-0277 按智能体服务器页的样式重排）：
// 页头（刷新、添加服务器）→ 统计卡（overview 的全量）→ 服务器表（搜索与状态筛选）→ 选中的服务器（?host_id=）显示详情：
// 页头卡片、连接与部署、这台服务器上的容器、模块区块、回收策略。没选服务器时，下面是全部容器、智能体用量、环境模板、执行记录四个标签。
// 标签、筛选与选中的容器写在地址里（?tab= / ?instance_id= / ?lifetime= / ?state= / ?q= / ?sort= / ?environment_id=），刷新、通知链接、后退都能回到同一处。
// 旧链接：?tab=environments → 全部容器（其余参数保留）；?tab=settings（&host_id=）→ 打开那台服务器（没写就打开第一台）。
// 宽屏（1440 以上）容器详情与“添加服务器”在右侧一栏，窄屏放进右侧抽屉；关掉容器详情后焦点回到打开它的那一行。
const TABS = [
  { key: 'overview', name: '全部容器' },
  { key: 'instances', name: '智能体用量' },
  { key: 'templates', name: '环境模板' },
  { key: 'runs', name: '执行记录' },
] as const
type Tab = typeof TABS[number]['key']
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id
const route = useRoute(), router = useRouter(), cache = useQueryClient()
const wide = typeof window.matchMedia === 'function' ? useMediaQuery('(min-width: 1440px)') : ref(false)

const one = (value: unknown) => (typeof value === 'string' && value ? value : undefined)
const tab = computed<Tab>(() => (TABS.some(t => t.key === route.query.tab) ? route.query.tab as Tab : 'overview'))
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
const selectedEnv = computed(() => one(route.query.environment_id) ?? null)
const hostId = computed(() => one(route.query.host_id) ?? null)

function update(patch: Record<string, string | undefined>, push = false) {
  const query: LocationQuery = { ...route.query }
  for (const [k, v] of Object.entries(patch)) { if (v === undefined || v === '') delete query[k]; else query[k] = v }
  void (push ? router.push({ query }) : router.replace({ query }))
}
function go(next: Tab) { update({ tab: next === 'overview' ? undefined : next }) }
function setFilters(next: EnvFilters) {
  update({ instance_id: next.instance_id, lifetime: next.lifetime, state: next.state, q: next.q, sort: next.sort === 'last_used' ? undefined : next.sort, include_deleted: next.include_deleted ? '1' : undefined })
}

const overview = useRunnerOverview(scope)
const data = computed(() => overview.data.value)
const hosts = computed<RunnerHost[]>(() => data.value?.hosts?.length ? data.value.hosts : data.value?.host ? [data.value.host] : [])
const selectedHost = computed(() => hosts.value.find(h => h.id === hostId.value) ?? null)
const instances = computed(() => data.value?.instances ?? [])
const counts = computed(() => summary(data.value?.environments))
const online = computed(() => hosts.value.filter(h => h.status === 'online').length)
const candidates = useQuery({
  queryKey: ['runner', user, scope, 'candidates'],
  enabled: computed(() => auth.user?.id === user && auth.workspaceId === scope && auth.canManage),
  queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/runner/hosts/candidates', { params: { header: { 'X-Workspace-Id': scope } }, signal })),
  refetchInterval: 30_000,
})
const candidate = computed(() => candidates.data.value?.items.find(c => c.id === hostId.value) ?? null)

// 旧链接：?tab=environments（原来的“环境”标签、旧通知）换成全部容器；?tab=settings（原来的“服务器设置”）打开服务器详情
if (route.query.tab === 'environments') update({ tab: undefined })
watch([() => route.query.tab, data], ([t, loaded]) => {
  if (t !== 'settings' || !loaded) return
  update({ tab: undefined, host_id: hostId.value ?? hosts.value[0]?.id })
}, { immediate: true })

// 服务器表：搜索与状态筛选（只在已经取回的列表里筛，统计数字仍来自 overview）
type Filter = 'all' | 'online' | 'pending' | 'attention'
const FILTERS: Record<Filter, string> = { all: '全部状态', online: '在线', pending: '等待接入', attention: '需要处理' }
const term = ref(''), status = ref<Filter>('all')
const attention = (h: RunnerHost) => h.status === 'offline' || h.status === 'disabled' || hostAlerts(h).length > 0
const shown = computed(() => {
  const q = term.value.trim().toLowerCase()
  return hosts.value.filter(h => (status.value === 'all' || (status.value === 'attention' ? attention(h) : h.status === status.value)) && (!q || h.name.toLowerCase().includes(q) || h.node_name?.toLowerCase().includes(q)))
})
const action = (h: RunnerHost) => h.status === 'pending' ? '部署' : '查看'
const containers = (h: RunnerHost) => hostMetrics(h).containers

// 打开 / 关闭服务器详情：换服务器时其余参数（标签、筛选、容器详情）都不带过去
function open(id: string | null) {
  adding.value = false
  void router.push({ query: id ? { host_id: id } : {} })
}

// 右侧一栏：添加服务器，或者容器详情（只开一个）
const adding = ref(false)
function openAdd() { adding.value = true; if (selectedEnv.value) update({ environment_id: undefined }) }
function added(host: RunnerHost) { void overview.refetch(); open(host.id) }
let opener: HTMLElement | null = null
function select(id: string, row: HTMLElement | null) { adding.value = false; opener = row; update({ environment_id: id }, true) }
async function close() {
  update({ environment_id: undefined })
  const back = opener
  opener = null
  await nextTick()
  if (back?.isConnected) back.focus()
  else document.querySelector<HTMLElement>(`tr[data-env="${CSS.escape(selectedEnv.value ?? '')}"]`)?.focus()
}
function agentContainers(instanceId: string) { update({ tab: undefined, instance_id: instanceId, state: undefined, lifetime: undefined, q: undefined }) }
const runsFor = ref<string | undefined>()
function showRuns(environmentId: string) { runsFor.value = environmentId; void router.push({ query: { tab: 'runs' } }) }
watch(tab, (t) => { if (t !== 'runs') runsFor.value = undefined })
function failed() { void router.push({ query: { state: 'failed' } }) }

const side = computed<'add' | 'env' | null>(() => adding.value ? 'add' : selectedEnv.value ? 'env' : null)
const reload = ref(0)
const refreshing = computed(() => overview.isFetching.value)
async function refresh() {
  reload.value += 1
  await cache.invalidateQueries({ predicate: q => q.queryKey[0] === 'runner' && q.queryKey[1] === user && q.queryKey[2] === scope })
}
const value = (n: number | null | undefined) => (n === null || n === undefined ? '—' : String(n))
</script>

<template>
  <header class="ws-header">
    <div>
      <h1>容器服务器</h1>
      <p>管理运行容器的服务器，查看 OpenClaw 申请的容器。添加服务器后通过 SSH 部署，智能体按需申请容器。</p>
    </div>
    <div class="ws-actions">
      <button type="button" class="secondary-button" :disabled="refreshing" @click="refresh"><RefreshCw aria-hidden="true" />刷新</button>
      <button type="button" class="primary-button" :disabled="adding" @click="openAdd"><Plus aria-hidden="true" />添加服务器</button>
    </div>
  </header>

  <p v-if="overview.isPending.value" role="status" class="ws-state-line">正在加载…</p>
  <div v-else-if="overview.error.value && !data" class="ws-state-line"><p role="alert">{{ runnerError(overview.error.value) }}</p><button type="button" class="secondary-button" @click="overview.refetch()">重试</button></div>
  <div v-else-if="data" class="asv-layout" :class="{ 'has-side': !!side && wide }">
    <div class="asv-main">
      <section class="ag-summary" aria-label="容器统计">
        <dl class="ag-tiles">
          <div class="ag-tile tone-blue"><dt><ServerIcon aria-hidden="true" />服务器</dt><dd>{{ hosts.length }}</dd></div>
          <div class="ag-tile tone-green"><dt><CircleCheck aria-hidden="true" />在线</dt><dd>{{ online }}</dd></div>
          <div class="ag-tile tone-violet" title="本工作空间未删除的环境记录（含已归档与创建中，不是此刻 Docker 里的容器数）"><dt><Box aria-hidden="true" />容器环境</dt><dd>{{ value(counts?.total) }}</dd></div>
          <div class="ag-tile tone-amber"><dt><CirclePlay aria-hidden="true" />运行中</dt><dd>{{ value(counts?.running) }}</dd></div>
        </dl>
        <p v-if="counts?.attention" class="ag-note" role="note">{{ counts.attention }} 个容器环境失败，需要关注。<button type="button" class="asv-link" @click="failed">查看</button></p>
        <p v-if="overview.error.value" role="alert">刷新失败，保留上次的服务器与环境列表：{{ runnerError(overview.error.value) }}</p>
      </section>

      <section class="ws-panel asv-list" aria-label="服务器列表">
        <div class="ws-toolbar">
          <label class="ws-search"><Search aria-hidden="true" /><span class="sr-only">搜索服务器名称</span><input v-model="term" type="search" placeholder="服务器名称" maxlength="100" /></label>
          <select v-model="status" aria-label="按状态筛选服务器"><option v-for="(label, k) in FILTERS" :key="k" :value="k">{{ label }}</option></select>
        </div>
        <p v-if="!hosts.length" class="ws-state-line">还没有容器服务器。智能体要在容器里执行命令，先点“添加服务器”添加一台专用的 Linux 服务器，再在页面上通过 SSH 部署。</p>
        <p v-else-if="!shown.length" class="ws-state-line">没有符合筛选条件的服务器。</p>
        <div v-else class="ws-table-wrap">
          <table class="ws-table asv-table asv-servers cs-servers">
            <thead><tr><th scope="col">服务器</th><th scope="col">入口</th><th scope="col">系统 / 资源</th><th scope="col">运行时</th><th scope="col">接入状态</th><th scope="col">容器</th><th scope="col"><span class="sr-only">操作</span></th></tr></thead>
            <tbody>
              <tr v-for="h in shown" :key="h.id" :aria-selected="hostId === h.id" @click="open(h.id)">
                <th scope="row" data-label="服务器"><span class="asv-name"><ServerIcon aria-hidden="true" /><span>{{ h.name }}</span></span><span v-if="h.backend === 'kubernetes'" class="badge">Kubernetes</span></th>
                <td data-label="入口">{{ INGRESS[h.ingress] }}</td>
                <td data-label="系统 / 资源" class="asv-nowrap">{{ hostResources(h) }}</td>
                <td data-label="运行时"><span v-if="h.runtimes.length" class="asv-state" :class="h.runtimes.includes('runsc') ? 'tone-green' : 'tone-amber'">{{ h.runtimes.includes('runsc') ? 'gVisor 可用' : '没装 gVisor' }}</span><span v-else class="asv-sub">部署后显示</span></td>
                <td data-label="接入状态"><span class="asv-state" :class="`tone-${HOST_TONE[h.status]}`">{{ HOST_STATUS[h.status] }}</span><span class="asv-sub">{{ h.last_seen_at ? `最近联系 ${ago(h.last_seen_at)}` : '还没有连上' }}</span></td>
                <td data-label="容器">{{ containers(h) === null ? '—' : `${containers(h)} 个` }}<span v-if="hostAlerts(h).length" class="asv-sub cs-alert-count">{{ hostAlerts(h).length }} 条告警</span></td>
                <td class="asv-link-cell"><button type="button" class="asv-link" :aria-label="`${action(h)}：${h.name}`" @click.stop="open(h.id)">{{ action(h) }}</button></td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-if="hosts.length" class="asv-foot">显示 {{ shown.length }} 台，共 {{ hosts.length }} 台</p>
      </section>

      <RunnerServerDetail
        v-if="selectedHost" :key="selectedHost.id" :host="selectedHost" :candidate="candidate" :candidates-failed="!!candidates.error.value"
        :instances="instances" :selected-env="selectedEnv" :reload="reload" @close="open(null)" @removed="open(null)" @select="select"
      />
      <section v-else-if="hostId" class="ws-panel ws-panel-body" aria-label="找不到这台容器服务器">
        <p role="alert">找不到这台容器服务器：可能已被移除，或你已无权访问。</p>
        <div class="form-actions"><button type="button" class="secondary-button" @click="open(null)">关闭</button></div>
      </section>

      <template v-else>
        <div class="cs-tabbar" role="tablist" aria-label="容器服务器">
          <button v-for="t in TABS" :key="t.key" type="button" role="tab" :aria-selected="tab === t.key" @click="go(t.key)">{{ t.name }}</button>
        </div>
        <div role="tabpanel" :aria-label="TABS.find(t => t.key === tab)?.name" class="cs-tabpanel">
          <OverviewTab v-if="tab === 'overview'" :instances="instances" :filters="filters" :workspace-total="counts?.total ?? null" :selected-id="selectedEnv" :recent="data.recent" :reload="reload" @filters="setFilters" @select="select" />
          <AgentUsageTab v-else-if="tab === 'instances'" :instances="instances" @containers="agentContainers" />
          <TemplatesTab v-else-if="tab === 'templates'" />
          <RunsTab v-else :instances="instances" :environment-id="runsFor" @clear="runsFor = undefined" />
        </div>
      </template>
    </div>

    <aside v-if="side && wide" class="ws-panel asv-side" :class="{ 'cs-side': side === 'env' }" :aria-label="side === 'add' ? '添加容器服务器' : '容器详情'">
      <template v-if="side === 'add'">
        <div class="asv-side-head">
          <h2>添加容器服务器</h2>
          <button type="button" class="icon-button" aria-label="关闭：添加容器服务器" @click="adding = false"><X aria-hidden="true" /></button>
        </div>
        <RunnerAddForm @saved="added" @close="adding = false" />
      </template>
      <ContainerDetailPanel v-else :key="selectedEnv!" :environment-id="selectedEnv!" aside @close="close" @runs="showRuns" />
    </aside>
  </div>
  <DrawerDetail v-if="side === 'add' && !wide" title="添加容器服务器" @close="adding = false">
    <RunnerAddForm @saved="added" @close="adding = false" />
  </DrawerDetail>
  <DrawerDetail v-else-if="side === 'env' && !wide" title="容器详情" @close="close">
    <ContainerDetailPanel :key="selectedEnv!" :environment-id="selectedEnv!" @close="close" @runs="showRuns" />
  </DrawerDetail>
</template>
