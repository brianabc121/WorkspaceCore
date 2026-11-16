<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useQueryClient } from '@tanstack/vue-query'
import { ArrowRight, Cpu, HardDrive, Info, MemoryStick, Package, Plug, RefreshCw, Rocket, Server as ServerIcon, X } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf } from '../../services/api'
import { formatTime } from '../time'
import { agentError } from './queries'
import { useAgentAction } from './action'
import { ALERT, DISK_USED_MAX, RANGE, bytes, load, percent, type HostRange } from './host'
import {
  RESOURCE_ERROR, SERVER_KIND, agentMemPercent, blockReason, canLogin, connectionNotice, connectionStatus, endpoint, initStatus, latencyText, latencyTone, mib,
  putServerDetail, useServer, usedPercent, type Server, type ServerDetail as Detail,
} from './servers'
import { sshError } from './ssh'
import HostCards from './HostCards.vue'
import HostTrends from './HostTrends.vue'
import ShareBar from './ShareBar.vue'
import AgentTable from './AgentTable.vue'
import AgentTrend from './AgentTrend.vue'
import ServerInitializationPanel from './ServerInitializationPanel.vue'
import ServerAgentsTable from './ServerAgentsTable.vue'
import OpsPanel from './OpsPanel.vue'
import ModuleServerPanels from '../../modules/ModuleServerPanels.vue'

// 服务器详情（T-0147；独立页面 T-0181，效果图 01 下半部分；页头按 T-0269 效果图收成一行）：名称、地址，
// 连接（右上角刷新图标重新测试，连接正常时显示延迟；T-0271）、服务器环境、CPU（核数与总使用率）、内存（已用 / 可用）、硬盘（总量与挂载盘）、前往部署；
// CPU / 内存 / 硬盘优先用平台的 SSH 资源采样（初始化后约每分钟一次，“刷新资源”立即再采一次），没有采样时退回智能体上报，都没有就说明原因，不写 0；
// 连接与初始化；“已部署 OpenClaw”清单（跳到智能体详情）；整机负载与趋势、各智能体负载；服务器级运维。
// “已部署 OpenClaw”后面是插拔式模块提供的区块（例如大模型客户端的“MCP 管理器”，T-0234）：经 ModuleServerPanels 渲染，这里不导入模块的代码。
const props = defineProps<{ serverId: string; range: HostRange }>()
const emit = defineEmits<{ back: []; edit: [mode: 'credentials' | 'address', server: Server]; range: [range: HostRange] }>()
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id, cache = useQueryClient()
const selected = ref<string | null>(null)
const detail = useServer(scope, () => props.serverId, () => props.range)
// 只显示当前这台服务器的数据：切换服务器时上一台的详情（keepPreviousData）不会顶替
const data = computed(() => detail.data.value?.server.id === props.serverId ? detail.data.value : undefined)
const server = computed(() => data.value?.server)
const { busy, error, notice, failure, act, refresh, header } = useAgentAction(scope, sshError)
const renaming = ref(false), name = ref('')
// 页头的两个刷新按钮各自转圈：重新测试连接 / 刷新资源
const doing = ref<'test' | 'resources' | null>(null)
watch(() => props.serverId, () => { selected.value = null; renaming.value = false })

const init = computed(() => server.value ? initStatus(server.value) : null)
const connection = computed(() => server.value ? connectionStatus(server.value) : null)
const current = computed(() => data.value?.current ?? null)
const running = computed(() => data.value?.job?.status === 'running')
const loginOk = computed(() => !!server.value && canLogin(server.value))
const latency = computed(() => {
  const ms = server.value?.ssh?.connection_latency_ms
  return ms === null || ms === undefined ? null : { text: latencyText(ms), tone: latencyTone(ms) }
})

// CPU / 内存 / 硬盘：平台的 SSH 采样优先；没有采样时退回智能体的整机上报（自动识别的服务器只有上报）
const resources = computed(() => server.value?.resources ?? null)
const sample = computed(() => resources.value?.sample ?? null)
const prepared = computed(() => !!server.value?.ssh?.prepared_at)
const reportNote = computed(() => server.value?.reported_at ? `最近上报 ${formatTime(server.value.reported_at)}${server.value.stale ? '（已过期）' : ''}` : '')
const cpu = computed(() => {
  const s = sample.value, c = current.value
  if (s) return { percent: s.cpu_percent, cores: s.cores, note: `1 分钟负载 ${load(s.load1)}` }
  if (c && c.cpu_percent !== null) return { percent: c.cpu_percent, cores: c.cores, note: reportNote.value }
  return null
})
const mem = computed(() => {
  const s = sample.value, c = current.value
  if (s) return { used: s.mem_used, total: s.mem_total, available: s.mem_available }
  if (c && c.mem_total && c.mem_used !== null) return { used: c.mem_used, total: c.mem_total, available: c.mem_available }
  return null
})
const disk = computed(() => {
  const s = sample.value, d = current.value?.root_disk ?? current.value?.data_disk
  if (s) return { used: s.storage_used, total: s.storage_total, available: s.storage_available, note: s.disks.length ? `${s.disks.length} 个挂载盘` : 'Docker 数据目录所在分区' }
  if (d) return { used: d.used, total: d.total, available: d.free, note: 'Docker 数据目录所在分区' }
  return null
})
// 没有数值时写清楚原因
const empty = computed(() => {
  const s = server.value, r = resources.value
  if (!s || s.kind !== 'ssh') return { value: '暂无上报', note: '智能体上线后每分钟上报' }
  if (!prepared.value) return { value: '初始化后显示', note: '初始化完成后平台每分钟采样一次' }
  if (r?.error) return { value: '采样失败', note: RESOURCE_ERROR[r.error] }
  return { value: '等待采样', note: '平台每分钟采样一次，也可以点“刷新资源”' }
})
const resourceNote = computed(() => {
  const r = resources.value, s = sample.value
  if (s) return `平台用 SSH 每分钟采样一次 · 最近采样 ${formatTime(s.sampled_at)}${r?.stale ? '（已过期）' : ''}${r?.error ? ` · ${RESOURCE_ERROR[r.error]}` : ''}`
  if (!prepared.value) return '初始化完成后，平台每分钟用 SSH 采样一次 CPU、内存与硬盘。'
  if (r?.error) return `${RESOURCE_ERROR[r.error]}。`
  return '还没有采样：平台每分钟采样一次，也可以点“刷新资源”立即采样。'
})
const diskPercent = (d: { used: number; total: number }) => usedPercent(d.used, d.total)
const memPercent = computed(() => current.value?.mem_total && current.value.mem_used !== null ? (100 * current.value.mem_used) / current.value.mem_total : null)
const bar = (value: number | null | undefined) => ({ width: `${Math.min(100, Math.max(0, value ?? 0))}%` })
const deployed = computed(() => (data.value?.agents ?? []).filter(a => a.deployment_state !== 'associated').length)
const allocated = computed(() => {
  const s = server.value
  if (!s || !s.managed_deployments) return null
  return {
    cpu: s.allocated_cpus === null ? '含不限制' : `${s.allocated_cpus} 核`,
    mem: s.allocated_mem_limit_mb === null ? '含不限制' : mib(s.allocated_mem_limit_mb),
  }
})

// 颜色跟着智能体：按名字的固定顺序给 1…8 号色（排序、筛选都不改）
const ordered = computed(() => [...(data.value?.agents ?? [])].sort((a, b) => a.name.localeCompare(b.name, 'zh-CN') || a.instance_id.localeCompare(b.instance_id)))
const slots = computed(() => new Map(ordered.value.map((a, i) => [a.instance_id, i + 1])))
const cpuShares = computed(() => ordered.value.map(a => ({ key: a.instance_id, label: a.name, value: a.load?.available ? a.load.cpu_percent : null })))
const memShares = computed(() => ordered.value.map(a => ({ key: a.instance_id, label: a.name, value: a.load?.available ? agentMemPercent(a, current.value?.mem_total) : null })))
const memDetail = (value: number) => bytes(((current.value?.mem_total ?? 0) * value) / 100)
const selectedRow = computed(() => data.value?.agents.find(a => a.instance_id === selected.value) ?? null)

function settle() { if (failure.value instanceof ApiError && failure.value.code === 'WRITE_UNKNOWN') void refresh() }

// 重新测试连接：和“连接与初始化”里的“测试连接”是同一个接口（用保存的密码登录、只读检查），结果里带延迟
async function testConnection() {
  const s = server.value
  if (!s) return
  doing.value = 'test'
  let next: Detail | null = null
  const ok = await act(async () => {
    next = dataOf(await auth.slowApi.POST('/api/v1/agents/servers/{server_id}/test-connection', { params: { header: { ...header, 'Idempotency-Key': crypto.randomUUID() }, path: { server_id: s.id } }, body: { version: s.version } }))
    putServerDetail(cache, user, scope, next)
  })
  doing.value = null
  const result = (next as Detail | null)?.server.ssh
  if (ok && result) notice.value = connectionNotice(result)
  settle()
}

// 刷新资源：立即用 SSH 再采一次 CPU、内存与硬盘（与测试连接共用每分钟 10 次的限流）
async function refreshResources() {
  const s = server.value
  if (!s) return
  doing.value = 'resources'
  await act(async () => putServerDetail(cache, user, scope, dataOf(await auth.slowApi.POST('/api/v1/agents/servers/{server_id}/resources/refresh', { params: { header, path: { server_id: s.id } } }))), '资源已刷新')
  doing.value = null
}

function startRename() { name.value = server.value?.name ?? ''; renaming.value = true }
async function rename() {
  const s = server.value
  const next = name.value.trim()
  if (!s || !next) return
  const ok = await act(async () => {
    const updated = dataOf(await auth.api.PATCH('/api/v1/agents/servers/{server_id}', { params: { header, path: { server_id: s.id } }, body: { name: next, version: s.version } }))
    if (data.value) putServerDetail(cache, user, scope, { ...data.value, server: updated })
  }, '名称已保存')
  if (ok) renaming.value = false
}
</script>

<template>
  <div class="asv-detail">
    <p v-if="detail.isPending.value" role="status" class="ws-state-line">正在加载服务器…</p>
    <div v-else-if="detail.error.value && !data" class="ws-panel ws-panel-body">
      <p role="alert">{{ agentError(detail.error.value) }}</p>
      <div class="form-actions"><button type="button" class="secondary-button" @click="emit('back')">关闭详情</button></div>
    </div>
    <template v-else-if="data && server">
      <section class="ws-panel asv-head" :aria-label="`服务器：${server.name}`">
        <div class="asv-head-top">
          <span class="asv-icon" aria-hidden="true"><ServerIcon /></span>
          <div class="asv-head-title">
            <h2 v-if="!renaming">{{ server.name }} <span v-if="init" class="asv-state" :class="`tone-${init.tone}`">{{ init.label }}</span><span v-if="server.kind === 'auto'" class="badge">{{ SERVER_KIND.auto }}</span></h2>
            <form v-else class="asv-rename" aria-label="改名称" @submit.prevent="rename">
              <label class="sr-only" for="asv-rename-input">名称</label>
              <input id="asv-rename-input" v-model.trim="name" maxlength="100" />
              <button type="submit" class="primary-button" :disabled="busy || !name.trim()">保存</button>
              <button type="button" class="secondary-button" @click="renaming = false">取消</button>
            </form>
            <p class="asv-sub">
              <template v-if="server.ssh">{{ endpoint(server.ssh.host, server.ssh.port) }} · {{ server.ssh.username }}</template>
              <template v-else>按实例上报的服务器指纹自动识别</template>
            </p>
          </div>
          <div class="asv-head-actions">
            <button v-if="!renaming" type="button" class="secondary-button" :disabled="busy" @click="startRename">改名称</button>
            <button type="button" class="icon-button asv-close" aria-label="关闭服务器详情" @click="emit('back')"><X aria-hidden="true" /></button>
          </div>
        </div>

        <ul class="asv-stats" aria-label="服务器概览">
          <li>
            <div class="asv-stat-head">
              <span class="asv-stat-label"><Plug aria-hidden="true" />连接</span>
              <button v-if="server.kind === 'ssh'" type="button" class="asv-stat-action" aria-label="重新检测连接" title="重新检测连接" :aria-busy="doing === 'test'" :disabled="busy || running || !loginOk" @click="testConnection"><RefreshCw :class="{ 'asv-spin': doing === 'test' }" aria-hidden="true" /></button>
            </div>
            <span class="asv-stat-value"><span v-if="connection" class="asv-state" :class="`tone-${connection.tone}`">{{ connection.label }}</span><span v-if="latency" class="asv-latency" :class="`tone-${latency.tone}`" title="SSH 连接的往返延迟">延迟 {{ latency.text }}</span></span>
            <span class="asv-stat-note">{{ server.ssh ? (server.ssh.last_connection_at ? `${formatTime(server.ssh.last_connection_at)} 检测` : '还没有检测') : server.reported_at ? `${formatTime(server.reported_at)} 上报` : '没有上报' }}</span>
          </li>
          <li>
            <span class="asv-stat-label"><Package aria-hidden="true" />服务器环境</span>
            <span class="asv-stat-value"><span v-if="init" class="asv-state" :class="`tone-${init.tone}`">{{ init.label }}</span></span>
            <span v-if="server.ssh?.prepared_at && server.initialization_status === 'ready'" class="asv-stat-note">{{ formatTime(server.ssh.prepared_at) }} 完成</span>
          </li>
          <li class="asv-stat-usage">
            <span class="asv-stat-label"><Cpu aria-hidden="true" />CPU</span>
            <template v-if="cpu"><span class="asv-stat-value"><b>{{ percent(cpu.percent) }}</b><span v-if="cpu.cores" class="asv-stat-sub">{{ cpu.cores }} 核</span></span><span class="host-bar" aria-hidden="true"><span :style="bar(cpu.percent)" /></span><span v-if="cpu.note" class="asv-stat-note">{{ cpu.note }}</span></template>
            <template v-else><span class="asv-stat-value asv-none">{{ empty.value }}</span><span class="asv-stat-note">{{ empty.note }}</span></template>
          </li>
          <li class="asv-stat-usage">
            <span class="asv-stat-label"><MemoryStick aria-hidden="true" />内存</span>
            <template v-if="mem"><b class="asv-stat-value">{{ bytes(mem.used) }} / {{ bytes(mem.total) }}</b><span class="host-bar" aria-hidden="true"><span :style="bar(usedPercent(mem.used, mem.total))" /></span><span class="asv-stat-note">已用 {{ percent(usedPercent(mem.used, mem.total)) }} · 可用 {{ bytes(mem.available) }}</span></template>
            <template v-else><span class="asv-stat-value asv-none">{{ empty.value }}</span><span class="asv-stat-note">{{ server.kind === 'ssh' ? empty.note : reportNote || empty.note }}</span></template>
          </li>
          <li class="asv-stat-usage" :class="{ 'is-danger': disk && (diskPercent(disk) ?? 0) >= DISK_USED_MAX }">
            <span class="asv-stat-label"><HardDrive aria-hidden="true" />硬盘</span>
            <template v-if="disk"><b class="asv-stat-value">{{ bytes(disk.used) }} / {{ bytes(disk.total) }}</b><span class="host-bar" aria-hidden="true"><span :style="bar(diskPercent(disk))" /></span><span class="asv-stat-note">可用 {{ bytes(disk.available) }} · {{ disk.note }}</span></template>
            <template v-else><span class="asv-stat-value asv-none">{{ empty.value }}</span><span class="asv-stat-note">{{ empty.note }}</span></template>
          </li>
          <li>
            <span class="asv-stat-label"><Rocket aria-hidden="true" />部署智能体</span>
            <template v-if="server.kind === 'ssh' && server.deployable">
              <RouterLink class="asv-go asv-stat-value" :to="{ path: '/agents', query: { deploy: '1', server_id: server.id } }">前往部署智能体<ArrowRight aria-hidden="true" /></RouterLink>
              <span class="asv-stat-note">{{ data.agents.length ? `已有 ${data.agents.length} 个智能体` : '还没有智能体' }}</span>
            </template>
            <template v-else-if="server.kind === 'ssh'"><span class="asv-stat-value asv-none">暂不能部署</span><span class="asv-stat-note">{{ blockReason(server) }}</span></template>
            <template v-else><span class="asv-stat-value asv-none">不能自动部署</span><span class="asv-stat-note">自动识别的服务器不能自动部署。</span></template>
          </li>
        </ul>
        <div v-if="server.kind === 'ssh'" class="asv-res" role="group" aria-label="服务器资源">
          <div class="asv-res-head">
            <p class="asv-res-note" :class="{ 'is-warn': resources?.error || resources?.stale && sample }"><Info aria-hidden="true" />{{ resourceNote }}</p>
            <button type="button" class="secondary-button asv-res-refresh" :aria-busy="doing === 'resources'" :disabled="busy || running || !loginOk || !prepared" @click="refreshResources"><RefreshCw :class="{ 'asv-spin': doing === 'resources' }" aria-hidden="true" />刷新资源</button>
          </div>
          <ul v-if="sample?.disks.length" class="asv-disks" aria-label="挂载的硬盘">
            <li v-for="d in sample.disks" :key="d.device" :class="{ 'is-danger': (diskPercent(d) ?? 0) >= DISK_USED_MAX }">
              <span class="asv-disk-name"><HardDrive aria-hidden="true" /><b>{{ d.mount }}</b><span>{{ d.device }} · {{ d.fs_type }}</span></span>
              <span class="host-bar" aria-hidden="true"><span :style="bar(diskPercent(d))" /></span>
              <span class="asv-disk-usage">已用 {{ bytes(d.used) }}（{{ percent(diskPercent(d)) }}） · 可用 {{ bytes(d.available) }} · 共 {{ bytes(d.total) }}</span>
            </li>
          </ul>
        </div>
        <ul v-if="server.alerts.length" class="host-alerts" aria-label="正在提醒的告警">
          <li v-for="a in server.alerts" :key="a"><span class="host-alert-icon" aria-hidden="true">!</span>{{ ALERT[a] }}</li>
        </ul>
        <p v-if="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
      </section>

      <ServerInitializationPanel :detail="data" @edit="emit('edit', $event, server)" @removed="emit('back')" />

      <section class="ws-panel asv-section" aria-label="已部署 OpenClaw">
        <div class="asv-panel-head">
          <h3>已部署 OpenClaw · {{ data.agents.length }}</h3>
          <span class="asv-sub">平台部署记录 {{ server.managed_deployments }} 条 · 部署成功 {{ server.successful_deployments ?? '待核验' }}<template v-if="allocated"> · 已分配上限 {{ allocated.cpu }} / {{ allocated.mem }}（不是实际占用）</template></span>
        </div>
        <p v-if="server.deployment_counts_partial" class="form-help">有部署于旧版本、运行状态未知的记录：部署成功数待核验，不按 0 计算。</p>
        <p v-if="!data.agents.length" class="ws-state-line">这台服务器上还没有智能体。<template v-if="server.kind === 'ssh'">初始化完成后到 OpenClaw 智能体页“部署智能体”。</template></p>
        <ServerAgentsTable v-else :rows="data.agents" />
        <p class="form-help">一台服务器可部署多个智能体：每个智能体一个 Compose 项目、一个目录、一个自己的网络，互不影响。{{ deployed < data.agents.length ? '“归属”表示手动指定或按上报识别、不是经平台部署的。' : '' }}部署状态以部署记录与最近任务为准；容器是否在运行请在智能体详情的运维里查看。</p>
      </section>

      <ModuleServerPanels place="agentServer" :server-id="server.id" :server-name="server.name" />

      <section class="ws-panel asv-section" aria-label="整机负载">
        <div class="asv-panel-head"><h3>整机负载</h3></div>
        <p v-if="!current" class="form-help">暂无上报：上面的智能体部署好、每分钟上报一次后显示。</p>
        <template v-else>
          <HostCards :current="current" :alerts="server.alerts" />
          <div class="server-shares">
            <ShareBar title="CPU：每个智能体占多少" :total="current.cpu_percent" :shares="cpuShares" />
            <ShareBar title="内存：每个智能体占多少" :total="memPercent" :shares="memShares" :detail="memDetail" />
          </div>
          <div class="segmented host-range" role="group" aria-label="趋势的时间范围">
            <button v-for="(label, key) in RANGE" :key="key" type="button" :aria-pressed="range === key" @click="emit('range', key)">最近 {{ label }}</button>
          </div>
          <HostTrends :points="data.points" :range="range" :cores="current.cores" />
        </template>
      </section>

      <section v-if="data.agents.length" class="ws-panel asv-section" aria-label="各智能体负载">
        <div class="asv-panel-head"><h3>各智能体负载</h3></div>
        <AgentTable :rows="data.agents" :slots="slots" :selected="selected" @select="selected = $event" />
        <AgentTrend v-if="selectedRow" :key="selectedRow.instance_id" :instance-id="selectedRow.instance_id" :name="selectedRow.name" :range="range" />
      </section>

      <div v-if="server.kind === 'ssh'" class="ws-panel asv-section"><OpsPanel kind="server" :target-id="server.id" /></div>
    </template>
  </div>
</template>
