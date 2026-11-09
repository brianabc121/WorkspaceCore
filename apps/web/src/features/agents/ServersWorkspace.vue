<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useQueryClient } from '@tanstack/vue-query'
import { Box, CircleCheck, Hourglass, Plus, RefreshCw, Search, Server as ServerIcon, X } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { useMediaQuery } from '../../composables/useMediaQuery'
import { useCreateIntent } from '../../composables/useCreateIntent'
import DrawerDetail from '../../components/DrawerDetail.vue'
import { agentError, IM_STATUS } from './queries'
import { RANGE, type HostRange } from './host'
import { connectionStatus, endpoint, initStatus, resources, serverKey, useServers, type Server } from './servers'
import ServerDetail from './ServerDetail.vue'
import ServerConnectionForm from './ServerConnectionForm.vue'

// 智能体服务器页（T-0181，效果图 01；改造规范第 3.1 节）：统计（来自服务端 summary，不从可见行推算）、
// 服务器表（搜索与状态筛选）、选中服务器的详情（?server=，趋势范围 ?range=），添加 / 更新连接信息的抽屉。
// 宽屏（1440 以上）抽屉是页面右侧一栏，窄屏放进右侧抽屉。进页与刷新只读（GET），不登录服务器、不初始化。
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id
const route = useRoute(), router = useRouter(), cache = useQueryClient()
const servers = useServers(scope)
const wide = typeof window.matchMedia === 'function' ? useMediaQuery('(min-width: 1440px)') : ref(false)

const list = computed(() => servers.data.value)
const items = computed(() => list.value?.items ?? [])
const summary = computed(() => list.value?.summary ?? null)
const unassigned = computed(() => list.value?.unassigned ?? [])
const selected = computed(() => typeof route.query.server === 'string' && route.query.server ? route.query.server : null)
const range = computed<HostRange>(() => typeof route.query.range === 'string' && route.query.range in RANGE ? route.query.range as HostRange : '24h')
const value = (n: number | null | undefined) => (n === null || n === undefined ? '—' : String(n))

// 状态筛选：按初始化 / 连接状态分组（只在已经取回的完整列表里筛，统计数字仍来自服务端）
type Filter = 'all' | 'ready' | 'pending' | 'attention' | 'auto'
const FILTERS: Record<Filter, string> = { all: '全部状态', ready: '已就绪', pending: '待初始化', attention: '需要处理', auto: '自动识别' }
const q = ref(''), filter = ref<Filter>('all')
function attention(s: Server) {
  const ssh = s.ssh
  return !!ssh && (!ssh.password_set || !ssh.host_key_confirmed || ['auth_failed', 'unreachable', 'host_key_changed', 'credential_unreadable'].includes(ssh.connection_status)
    || s.initialization_status === 'failed' || s.initialization_status === 'interrupted' || ssh.legacy_key_cleanup_state === 'manual_required')
}
function matches(s: Server, f: Filter) {
  if (f === 'all') return true
  if (f === 'auto') return s.kind === 'auto'
  if (f === 'ready') return s.kind === 'ssh' && s.initialization_status === 'ready'
  if (f === 'pending') return s.kind === 'ssh' && (s.initialization_status === 'not_initialized' || s.initialization_status === 'initializing')
  return attention(s)
}
const shown = computed(() => {
  const term = q.value.trim().toLowerCase()
  return items.value.filter(s => matches(s, filter.value) && (!term || [s.name, s.ssh?.host, s.ssh?.address].some(v => v?.toLowerCase().includes(term))))
})
const action = (s: Server) => s.kind === 'auto' ? '查看' : !s.ssh?.password_set ? '补充密码' : s.initialization_status === 'ready' || s.initialization_status === 'initializing' ? '查看' : '初始化'

function open(id: string | null) {
  void router.push({ path: '/agent-servers', query: id ? { server: id, ...(range.value !== '24h' ? { range: range.value } : {}) } : {} })
}
function setRange(next: HostRange) {
  const query = { ...route.query }
  if (next === '24h') delete query.range; else query.range = next
  void router.replace({ query })
}

// 添加 / 更新连接信息的抽屉；切到别的服务器时关掉（密码随组件一起清掉）
type Connection = { mode: 'create' | 'credentials' | 'address'; server: Server | null }
const connection = ref<Connection | null>(null)
const formTitle = computed(() => !connection.value ? '' : connection.value.mode === 'create' ? '添加服务器' : connection.value.mode === 'address' ? '更换地址' : connection.value.server?.ssh?.password_set ? '更新登录密码' : '补充服务器登录密码')
const formKey = computed(() => connection.value ? `${connection.value.mode}:${connection.value.server?.id ?? 'new'}` : '')
function openAdd() { connection.value = { mode: 'create', server: null } }
function edit(mode: 'credentials' | 'address', server: Server) { connection.value = { mode, server } }
function closeConnection() { connection.value = null }
watch(selected, next => { if (connection.value?.server && connection.value.server.id !== next) connection.value = null })
// 顶栏“创建 → 添加智能体服务器”
useCreateIntent(() => true, openAdd)

const refreshing = computed(() => servers.isFetching.value)
async function refresh() {
  await Promise.all([servers.refetch(), selected.value ? cache.invalidateQueries({ queryKey: serverKey(user, scope, selected.value) }) : undefined])
}
</script>

<template>
  <header class="ws-header">
    <div>
      <h1>智能体服务器</h1>
      <p>管理服务器环境，查看其上部署的智能体。用 IP、SSH 端口、账号和密码添加，初始化后到“OpenClaw 智能体”部署。</p>
    </div>
    <div class="ws-actions">
      <button type="button" class="secondary-button" :disabled="refreshing" @click="refresh"><RefreshCw aria-hidden="true" />刷新</button>
      <button type="button" class="primary-button" :disabled="connection?.mode === 'create'" @click="openAdd"><Plus aria-hidden="true" />添加服务器</button>
    </div>
  </header>

  <div class="asv-layout" :class="{ 'has-side': !!connection && wide }">
    <div class="asv-main">
      <section class="ag-summary" aria-label="服务器统计">
        <dl class="ag-tiles">
          <div class="ag-tile tone-blue"><dt><ServerIcon aria-hidden="true" />服务器</dt><dd>{{ value(summary?.server_count) }}</dd></div>
          <div class="ag-tile tone-green"><dt><CircleCheck aria-hidden="true" />已就绪</dt><dd>{{ value(summary?.ready_count) }}</dd></div>
          <div class="ag-tile tone-violet"><dt><Box aria-hidden="true" />已部署智能体</dt><dd>{{ value(summary?.successful_deployments) }}<small v-if="summary && summary.successful_deployments === null">待核验</small></dd></div>
          <div class="ag-tile tone-amber"><dt><Hourglass aria-hidden="true" />待初始化</dt><dd>{{ value(summary?.not_initialized_count) }}</dd></div>
        </dl>
        <p v-if="summary?.deployment_counts_partial" class="form-help">“已部署智能体”待核验：有部署于旧版本、运行状态未知的记录，不按 0 计算。</p>
        <p v-if="summary?.needs_credentials_count" class="ag-note" role="note">{{ summary.needs_credentials_count }} 台服务器待补充登录密码：已有的智能体照常运行，补充后才能初始化、部署与运维。</p>
      </section>

      <section class="ws-panel asv-list" aria-label="服务器列表">
        <div class="ws-toolbar">
          <label class="ws-search"><Search aria-hidden="true" /><span class="sr-only">搜索服务器名称或 IP</span><input v-model="q" type="search" placeholder="服务器名称或 IP" maxlength="100" /></label>
          <select v-model="filter" aria-label="按状态筛选服务器"><option v-for="(label, key) in FILTERS" :key="key" :value="key">{{ label }}</option></select>
        </div>
        <p v-if="servers.isPending.value" role="status" class="ws-state-line">正在加载服务器…</p>
        <div v-else-if="servers.error.value && !list" class="ws-state-line"><p role="alert">{{ agentError(servers.error.value) }}</p><button type="button" class="secondary-button" @click="servers.refetch()">重试</button></div>
        <p v-else-if="!items.length" class="ws-state-line">还没有服务器。点“添加服务器”用 IP、账号和密码添加一台；手动部署的实例上报后也会自动出现在这里。</p>
        <p v-else-if="!shown.length" class="ws-state-line">没有符合筛选条件的服务器。</p>
        <div v-else class="ws-table-wrap">
          <table class="ws-table asv-table asv-servers">
            <thead><tr><th scope="col">服务器</th><th scope="col">连接地址</th><th scope="col">系统 / 资源</th><th scope="col">初始化状态</th><th scope="col">连接状态</th><th scope="col">智能体</th><th scope="col"><span class="sr-only">操作</span></th></tr></thead>
            <tbody>
              <tr v-for="s in shown" :key="s.id" :aria-selected="selected === s.id" @click="open(s.id)">
                <th scope="row" data-label="服务器"><span class="asv-name"><ServerIcon aria-hidden="true" /><span>{{ s.name }}</span></span><span v-if="s.kind === 'auto'" class="badge">自动识别</span></th>
                <td data-label="连接地址" class="asv-endpoint">{{ s.ssh ? endpoint(s.ssh.host, s.ssh.port) : '—' }}</td>
                <td data-label="系统 / 资源" class="asv-nowrap">{{ resources(s) }}</td>
                <td data-label="初始化状态"><span class="asv-state" :class="`tone-${initStatus(s).tone}`">{{ initStatus(s).label }}</span></td>
                <td data-label="连接状态"><span class="asv-state" :class="`tone-${connectionStatus(s).tone}`">{{ connectionStatus(s).label }}</span></td>
                <td data-label="智能体">{{ s.instances_total }} 个<span class="asv-sub">{{ s.instances_online }} 个消息在线</span></td>
                <td class="asv-link-cell"><button type="button" class="asv-link" :aria-label="`${action(s)}：${s.name}`" @click.stop="open(s.id)">{{ action(s) }}</button></td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-if="items.length" class="asv-foot">显示 {{ shown.length }} 台，共 {{ items.length }} 台<template v-if="servers.error.value"> · 刷新失败，显示的是上次取回的数据</template></p>
      </section>

      <ServerDetail v-if="selected" :key="selected" :server-id="selected" :range="range" @back="open(null)" @edit="edit" @range="setRange" />

      <section v-if="unassigned.length && !selected" class="ws-panel asv-section" aria-label="还没归属服务器的实例">
        <div class="asv-panel-head"><h3>还没归属服务器的实例（{{ unassigned.length }}）</h3></div>
        <p class="form-help">它们还没上报过（或者接入包较旧）。在 OpenClaw 智能体页打开它，部署到一台服务器或手动指定所在服务器。</p>
        <ul class="asv-unassigned"><li v-for="u in unassigned" :key="u.id"><RouterLink :to="{ path: '/agents', query: { instance: u.id } }">{{ u.name }}</RouterLink> · {{ IM_STATUS[u.status] }}</li></ul>
      </section>
    </div>

    <aside v-if="connection && wide" class="ws-panel asv-side" :aria-label="formTitle">
      <div class="asv-side-head">
        <h2>{{ formTitle }}</h2>
        <button type="button" class="icon-button" :aria-label="`关闭：${formTitle}`" @click="closeConnection"><X aria-hidden="true" /></button>
      </div>
      <ServerConnectionForm :key="formKey" :mode="connection.mode" :server="connection.server" @close="closeConnection" @saved="open" />
    </aside>
  </div>

  <DrawerDetail v-if="connection && !wide" :title="formTitle" @close="closeConnection">
    <ServerConnectionForm :key="formKey" :mode="connection.mode" :server="connection.server" @close="closeConnection" @saved="open" />
  </DrawerDetail>
</template>
