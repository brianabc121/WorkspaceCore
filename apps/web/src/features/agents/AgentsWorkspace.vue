<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useQueryClient } from '@tanstack/vue-query'
import { Bot, CircleAlert, MessageCircle, Plus, RefreshCw, Rocket, Search, X } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { dataOf, type Schema } from '../../services/api'
import { useMediaQuery } from '../../composables/useMediaQuery'
import { useCreateIntent } from '../../composables/useCreateIntent'
import DrawerDetail from '../../components/DrawerDetail.vue'
import { IM_STATUS, agentError, downloadBundle, overviewKey, unique, useAgentOverview, useAgentSettings, useInstance, useInstances, useSecrets, type OverviewFilters } from './queries'
import { useAgentAction } from './action'
import { useHostSummary } from './host'
import { useServers } from './servers'
import { recallDeployment, type DeploymentRecord } from './deploymentRequests'
import AgentOverviewTable from './AgentOverviewTable.vue'
import AgentDetailPanel from './AgentDetailPanel.vue'
import AgentDeployDrawer from './AgentDeployDrawer.vue'
import InstanceCard from './InstanceCard.vue'
import InstanceForm from './InstanceForm.vue'
import { useMailAccounts } from '../mail/api'

// OpenClaw 智能体页（T-0182，效果图 02；改造规范第 3.2 节）：统计来自 GET /agents/overview 的 summary（不受分页与状态筛选影响），
// 表格支持搜索、服务器与状态筛选、分页；?instance= 打开详情（保留原有的全部能力），?deploy=1&server_id= 打开部署抽屉并预选服务器。
// 主操作“部署智能体”选一台已初始化的服务器自动部署；次操作“手动登记 / 接入”保留原来的接入包方式（含内网实例）。
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id
const route = useRoute(), router = useRouter(), cache = useQueryClient()
const settings = useAgentSettings(scope), instances = useInstances(scope), secrets = useSecrets(scope), hostSummary = useHostSummary(scope)
const servers = useServers(scope)
const mailAccounts = useMailAccounts(scope)
const mailboxes = computed(() => mailAccounts.data.value?.items ?? [])
const { busy, error, notice, act, header } = useAgentAction(scope)
const wide = typeof window.matchMedia === 'function' ? useMediaQuery('(min-width: 1440px)') : ref(false)

// 筛选：名称搜索（停顿 300 毫秒再查）、所属服务器、连接状态
const search = ref(''), q = ref(''), serverFilter = ref(''), statusFilter = ref<'' | Schema['AgentInstanceOut']['status']>('')
let timer: ReturnType<typeof setTimeout> | undefined
watch(search, value => { clearTimeout(timer); timer = setTimeout(() => { q.value = value.trim().slice(0, 100) }, 300) })
onBeforeUnmount(() => clearTimeout(timer))
const filters = computed<OverviewFilters>(() => ({ ...(serverFilter.value ? { server_id: serverFilter.value } : {}), ...(q.value ? { q: q.value } : {}), ...(statusFilter.value ? { status: statusFilter.value } : {}) }))
const overview = useAgentOverview(scope, () => filters.value)
const rows = computed(() => [...new Map((overview.data.value?.pages ?? []).flatMap(p => p.items).map(r => [r.instance.id, r])).values()])
const summary = computed(() => overview.data.value?.pages[0]?.summary ?? null)
const value = (n: number | undefined) => (n === undefined ? '—' : String(n))
const serverItems = computed(() => servers.data.value?.items ?? [])

// 详情：?instance=；不在已取回的列表里时单独取，找不到 / 失权时清空
const selectedId = computed(() => typeof route.query.instance === 'string' && route.query.instance ? route.query.instance : null)
const selectedRow = computed(() => rows.value.find(r => r.instance.id === selectedId.value) ?? null)
const single = useInstance(scope, () => selectedId.value, () => !selectedRow.value && !overview.isPending.value)
const selected = computed(() => selectedRow.value?.instance ?? (single.data.value?.id === selectedId.value && !single.error.value ? single.data.value ?? null : null))
const missing = computed(() => !!selectedId.value && !selectedRow.value && !!single.error.value)
function open(id: string) { if (id !== selectedId.value) void router.push({ path: '/agents', query: { instance: id } }) }
function closeDetail() { void router.push({ path: '/agents' }) }

// 详情要用的辅助数据（原来实例卡片就有）
const secretItems = computed(() => unique(secrets.data.value?.pages))
const names = computed(() => new Map(unique(instances.data.value?.pages).map(i => [i.id, i.name])))
const hosts = computed(() => new Map((hostSummary.data.value?.items ?? []).map(item => [item.instance_id, item])))
const serverName = (instance: Schema['AgentInstanceOut']) => { const id = hosts.value.get(instance.id)?.server_id ?? instance.server_id; return id ? serverItems.value.find(s => s.id === id)?.name : undefined }
const path = (instance: Schema['AgentInstanceOut']) => ({ instance_id: instance.id })

// 部署抽屉：?deploy=1&server_id= 打开并预选（参数用过就去掉，刷新不会再弹）；有没结论的部署请求时先找回它
const deploying = ref<{ preselect: string | null; recovery: DeploymentRecord | null; key: number } | null>(null)
let generation = 0
function openDeploy(preselect: string | null = null) { deploying.value = { preselect, recovery: recallDeployment(user, scope), key: ++generation } }
function closeDeploy() { deploying.value = null }
function viewDeployed(id: string) { if (!wide.value) closeDeploy(); open(id) }
watch(() => route.query.deploy, flag => {
  if (flag !== '1') return
  openDeploy(typeof route.query.server_id === 'string' && route.query.server_id ? route.query.server_id : null)
  const query = { ...route.query }
  delete query.deploy; delete query.server_id
  void router.replace({ query, hash: route.hash })
}, { immediate: true })
if (!deploying.value && recallDeployment(user, scope)) openDeploy()
// 顶栏“创建 → 部署 OpenClaw 智能体”
useCreateIntent(() => !busy.value, () => openDeploy())

const editing = ref<Schema['AgentInstanceOut'] | 'new' | null>(null)
const refreshing = computed(() => overview.isFetching.value || servers.isFetching.value)
function refresh() {
  void cache.invalidateQueries({ queryKey: overviewKey(user, scope) })
  void servers.refetch()
  if (selectedId.value) void cache.invalidateQueries({ queryKey: ['agents', user, scope, 'deployment', selectedId.value] })
}

function toggleAll() {
  const s = settings.data.value
  if (!s) return
  if (s.agents_enabled && !window.confirm('关闭总开关后所有实例立即失去访问权限（MCP、取密钥、IM），确认关闭？')) return
  void act(() => auth.api.PATCH('/api/v1/agents/settings', { params: { header }, body: { agents_enabled: !s.agents_enabled, version: s.version } }), s.agents_enabled ? '总开关已关闭' : '总开关已打开')
}
async function save(body: Schema['AgentInstanceCreate'] | Schema['AgentInstanceUpdate']) {
  const target = editing.value
  let created: string | null = null
  const ok = await act(async () => {
    if (target && target !== 'new') dataOf(await auth.api.PATCH('/api/v1/agents/instances/{instance_id}', { params: { header, path: path(target) }, body: body as Schema['AgentInstanceUpdate'] }))
    else created = dataOf(await auth.api.POST('/api/v1/agents/instances', { params: { header }, body: body as Schema['AgentInstanceCreate'] })).id
  }, target === 'new' ? '实例已登记：下面是它的详情，生成接入包后按 CHECKLIST.md 部署' : '实例已保存')
  if (!ok) return
  editing.value = null
  if (created) open(created)
}
function toggle(instance: Schema['AgentInstanceOut']) {
  const disabling = instance.status !== 'disabled'
  if (disabling && !window.confirm(`停用「${instance.name}」？它会立即失去访问权限并被踢下线。`)) return
  void act(() => disabling
    ? auth.api.POST('/api/v1/agents/instances/{instance_id}/disable', { params: { header, path: path(instance) }, body: { version: instance.version } })
    : auth.api.POST('/api/v1/agents/instances/{instance_id}/enable', { params: { header, path: path(instance) }, body: { version: instance.version } }), disabling ? '实例已停用' : '实例已启用')
}
function bundle(instance: Schema['AgentInstanceOut'], previous: Schema['BundleRequest']['previous']) {
  void act(() => downloadBundle(scope, instance.id, { version: instance.version, previous }), '接入包已生成并下载：凭证只在这个 zip 里，按里面的 CHECKLIST.md 部署')
}
function revoke(instance: Schema['AgentInstanceOut'], credential: Schema['AgentCredentialOut']) {
  if (!window.confirm('立即吊销这份凭证？使用它的 OpenClaw 会马上失去访问权限。')) return
  void act(() => auth.api.POST('/api/v1/agents/instances/{instance_id}/credentials/{credential_id}/revoke', { params: { header, path: { ...path(instance), credential_id: credential.id } }, body: { version: instance.version } }), '凭证已吊销')
}
function projectAdmin(instance: Schema['AgentInstanceOut'], enabled: boolean) {
  if (enabled && !window.confirm(`允许「${instance.name}」新建项目、把智能体拉进它建的项目？这些操作直接生效、不审批，每次都会通知管理员。`)) return
  void act(() => auth.api.PUT('/api/v1/agents/instances/{instance_id}/project-admin', { params: { header, path: path(instance) }, body: { enabled, version: instance.version } }), enabled ? '已打开项目管理权限' : '已关闭项目管理权限')
}
function imMedia(instance: Schema['AgentInstanceOut'], enabled: boolean) {
  if (enabled && !window.confirm(`允许「${instance.name}」经 im_send 发图片、文件、视频？工作台会检查类型、大小和密钥，直接发出、不审批。`)) return
  void act(() => auth.api.PUT('/api/v1/agents/instances/{instance_id}/im-media', { params: { header, path: path(instance) }, body: { enabled, version: instance.version } }), enabled ? '已打开附件权限' : '已关闭附件权限')
}
function stopOthers(instance: Schema['AgentInstanceOut'], enabled: boolean) {
  if (enabled && !window.confirm(`允许「${instance.name}」叫停本工作空间里别的智能体正在执行的任务？叫停直接生效、不审批，每次都会通知管理员。`)) return
  void act(() => auth.api.PUT('/api/v1/agents/instances/{instance_id}/stop-others', { params: { header, path: path(instance) }, body: { enabled, version: instance.version } }), enabled ? '已打开终止其他智能体任务的权限' : '已关闭终止其他智能体任务的权限')
}
function mail(instance: Schema['AgentInstanceOut'], body: Omit<Schema['AgentMailPut'], 'version'>) {
  const opening = body.send_enabled && !instance.mail.send_enabled
  const range = body.send_scope === 'any' ? '任何地址' : (body.recipients ?? []).join('、')
  if (opening && !window.confirm(`允许「${instance.name}」用分配给它的 Gmail 邮箱发信（收件人范围：${range}）？发信直接生效、不审批，每封都会通知管理员。`)) return
  const before = new Set(instance.mail.accounts.map(a => a.id))
  const added = mailboxes.value.filter(m => (body.account_ids ?? []).includes(m.id) && !before.has(m.id)).map(m => m.email)
  if (added.length && !window.confirm(`把邮箱 ${added.join('、')} 分配给「${instance.name}」？它能查这些邮箱的邮件。`)) return
  void act(() => auth.api.PUT('/api/v1/agents/instances/{instance_id}/mail', { params: { header, path: path(instance) }, body: { ...body, version: instance.version } }), '邮件权限已保存')
}
function grants(instance: Schema['AgentInstanceOut'], list: Schema['SecretGrantIn'][]) {
  void act(() => auth.api.PUT('/api/v1/agents/instances/{instance_id}/secret-grants', { params: { header, path: path(instance) }, body: { grants: list, version: instance.version } }), '凭据授权已保存')
}
</script>

<template>
  <header class="ws-header">
    <div>
      <h1>OpenClaw 智能体</h1>
      <p>选择已初始化的服务器，自动部署独立容器；也可以手动登记、用接入包接入。只有 owner 可以管理。</p>
    </div>
    <div class="ws-actions">
      <button type="button" class="secondary-button" :disabled="refreshing" @click="refresh"><RefreshCw aria-hidden="true" />刷新</button>
      <button type="button" class="secondary-button" :disabled="busy" @click="editing = 'new'"><Plus aria-hidden="true" />手动登记 / 接入</button>
      <button type="button" class="primary-button" @click="openDeploy()"><Rocket aria-hidden="true" />部署智能体</button>
    </div>
  </header>

  <section class="ag-switch" aria-label="总开关">
    <p v-if="settings.isPending.value" role="status">正在加载总开关…</p>
    <p v-else-if="settings.error.value" role="alert">{{ agentError(settings.error.value) }}</p>
    <template v-else-if="settings.data.value">
      <p><strong>总开关：{{ settings.data.value.agents_enabled ? '打开' : '关闭' }}</strong><span class="asv-sub">关闭后所有实例立即失去访问权限（MCP、取密钥、IM）。</span></p>
      <button type="button" class="secondary-button" :class="{ 'im-danger': settings.data.value.agents_enabled }" :disabled="busy" @click="toggleAll">{{ settings.data.value.agents_enabled ? '关闭总开关' : '打开总开关' }}</button>
    </template>
  </section>
  <p v-if="error && !editing" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>

  <div class="asv-layout" :class="{ 'has-side': !!deploying && wide }">
    <div class="asv-main">
      <section class="ag-summary" aria-label="智能体统计">
        <dl class="ag-tiles">
          <div class="ag-tile tone-violet"><dt><Bot aria-hidden="true" />智能体总数</dt><dd>{{ value(summary?.total) }}</dd></div>
          <div class="ag-tile tone-green"><dt><MessageCircle aria-hidden="true" />消息在线</dt><dd>{{ value(summary?.online) }}</dd></div>
          <div class="ag-tile tone-blue"><dt><RefreshCw aria-hidden="true" />部署中</dt><dd>{{ value(summary?.deploying) }}</dd></div>
          <div class="ag-tile tone-amber"><dt><CircleAlert aria-hidden="true" />需关注</dt><dd>{{ value(summary?.attention) }}</dd></div>
        </dl>
        <p v-if="summary" class="form-help">待接入 {{ summary.pending }} · 离线 {{ summary.offline }} · 已停用 {{ summary.disabled }}。“部署中”与连接状态可能重叠，“需关注”是部署失败 / 中断或没停用但离线的智能体，几项不要求相加等于总数；消息在线不代表容器在运行。</p>
      </section>

      <section class="ws-panel asv-list" aria-label="智能体列表">
        <div class="ws-toolbar">
          <label class="ws-search"><Search aria-hidden="true" /><span class="sr-only">搜索智能体名称</span><input v-model="search" type="search" placeholder="搜索智能体" maxlength="100" /></label>
          <select v-model="serverFilter" aria-label="按服务器筛选"><option value="">全部服务器</option><option v-for="s in serverItems" :key="s.id" :value="s.id">{{ s.name }}</option></select>
          <select v-model="statusFilter" aria-label="按连接状态筛选"><option value="">全部状态</option><option v-for="(label, key) in IM_STATUS" :key="key" :value="key">{{ label }}</option></select>
        </div>
        <p v-if="overview.isPending.value" role="status" class="ws-state-line">正在加载智能体…</p>
        <div v-else-if="overview.error.value && !rows.length" class="ws-state-line"><p role="alert">{{ agentError(overview.error.value) }}</p><button type="button" class="secondary-button" @click="overview.refetch()">重试</button></div>
        <p v-else-if="!rows.length && !filters.q && !filters.status && !filters.server_id" class="ws-state-line">还没有智能体。点“部署智能体”选一台已初始化的服务器自动部署，或“手动登记 / 接入”。</p>
        <p v-else-if="!rows.length" class="ws-state-line">没有符合筛选条件的智能体。</p>
        <AgentOverviewTable v-else :rows="rows" :selected="selectedId" @select="open" />
        <footer v-if="rows.length" class="asv-foot ag-foot">
          <span>已显示 {{ rows.length }} 个<template v-if="summary && !filters.status">，共 {{ summary.total }} 个</template><template v-if="overview.error.value"> · 刷新失败，显示的是上次取回的数据</template></span>
          <button v-if="overview.hasNextPage.value" type="button" class="secondary-button" :disabled="overview.isFetchingNextPage.value" @click="overview.fetchNextPage()">加载更多智能体</button>
        </footer>
      </section>

      <section v-if="selectedId" class="ws-panel ag-detail" aria-label="智能体详情">
        <p v-if="!selected && !missing" role="status" class="ws-state-line">正在加载智能体…</p>
        <div v-else-if="missing" class="ws-state-line">
          <p role="alert">没有找到这个智能体，或你已无权访问。</p>
          <button type="button" class="secondary-button" @click="closeDetail">关闭</button>
        </div>
        <template v-else-if="selected">
          <AgentDetailPanel :key="selected.id" :instance="selected" :row="selectedRow" @close="closeDetail" />
          <InstanceCard
            :key="`card:${selected.id}`" :instance="selected" :secrets="secretItems" :busy="busy" :host="hosts.get(selected.id)" :names="names" :mailboxes="mailboxes" :server-name="serverName(selected)"
            @edit="editing = selected" @toggle="toggle(selected)" @bundle="bundle(selected, $event)" @revoke="revoke(selected, $event)" @grants="grants(selected, $event)" @project-admin="projectAdmin(selected, $event)" @im-media="imMedia(selected, $event)" @stop-others="stopOthers(selected, $event)" @mail="mail(selected, $event)"
          />
        </template>
      </section>
      <p v-else-if="rows.length" class="form-help ag-pick">点表格里的一行查看智能体详情：配置、权限、接入凭证、部署与运维、负载、唤醒、发件箱。</p>
    </div>

    <aside v-if="deploying && wide" class="ws-panel asv-side" aria-label="部署 OpenClaw">
      <div class="asv-side-head">
        <h2>部署 OpenClaw</h2>
        <button type="button" class="icon-button" aria-label="关闭：部署 OpenClaw" @click="closeDeploy"><X aria-hidden="true" /></button>
      </div>
      <AgentDeployDrawer :key="deploying.key" :servers="serverItems" :servers-loading="servers.isPending.value" :preselect="deploying.preselect" :recovery="deploying.recovery" @close="closeDeploy" @view="viewDeployed" />
    </aside>
  </div>

  <DrawerDetail v-if="deploying && !wide" title="部署 OpenClaw" @close="closeDeploy">
    <AgentDeployDrawer :key="deploying.key" :servers="serverItems" :servers-loading="servers.isPending.value" :preselect="deploying.preselect" :recovery="deploying.recovery" @close="closeDeploy" @view="viewDeployed" />
  </DrawerDetail>
  <InstanceForm v-if="editing" :instance="editing === 'new' ? undefined : editing" :busy="busy" :error="error" @close="editing = null" @save="save" />
</template>
