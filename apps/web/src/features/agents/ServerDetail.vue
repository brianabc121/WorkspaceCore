<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useQueryClient } from '@tanstack/vue-query'
import { ArrowRight, Cpu, MemoryStick, Server as ServerIcon, X } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { dataOf } from '../../services/api'
import { formatTime } from '../time'
import { agentError } from './queries'
import { useAgentAction } from './action'
import { ALERT, RANGE, bytes, percent, type HostRange } from './host'
import { SERVER_KIND, agentMemPercent, blockReason, connectionStatus, endpoint, initStatus, mib, putServerDetail, useServer, type Server } from './servers'
import { sshError } from './ssh'
import HostCards from './HostCards.vue'
import HostTrends from './HostTrends.vue'
import ShareBar from './ShareBar.vue'
import AgentTable from './AgentTable.vue'
import AgentTrend from './AgentTrend.vue'
import ServerInitializationPanel from './ServerInitializationPanel.vue'
import ServerAgentsTable from './ServerAgentsTable.vue'
import OpsPanel from './OpsPanel.vue'

// 服务器详情（T-0147；独立页面 T-0181，效果图 01 下半部分）：名称、地址与真实 CPU / 内存（没有上报写“暂无上报”，不写 0）、
// 前往部署；连接与初始化；“已部署 OpenClaw”清单（跳到智能体详情）；整机负载与趋势、各智能体负载；服务器级运维。
const props = defineProps<{ serverId: string; range: HostRange }>()
const emit = defineEmits<{ back: []; edit: [mode: 'credentials' | 'address', server: Server]; range: [range: HostRange] }>()
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id, cache = useQueryClient()
const selected = ref<string | null>(null)
const detail = useServer(scope, () => props.serverId, () => props.range)
// 只显示当前这台服务器的数据：切换服务器时上一台的详情（keepPreviousData）不会顶替
const data = computed(() => detail.data.value?.server.id === props.serverId ? detail.data.value : undefined)
const server = computed(() => data.value?.server)
const { busy, error, notice, act, header } = useAgentAction(scope, sshError)
const renaming = ref(false), name = ref('')
watch(() => props.serverId, () => { selected.value = null; renaming.value = false })

const init = computed(() => server.value ? initStatus(server.value) : null)
const connection = computed(() => server.value ? connectionStatus(server.value) : null)
const current = computed(() => data.value?.current ?? null)
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

        <div class="asv-head-body">
          <ul class="asv-meters" aria-label="整机当前用量">
            <li>
              <span class="asv-meter-label"><Cpu aria-hidden="true" />CPU</span>
              <template v-if="current && current.cpu_percent !== null"><b>{{ percent(current.cpu_percent) }}</b><span class="host-bar" aria-hidden="true"><span :style="bar(current.cpu_percent)" /></span></template>
              <span v-else class="asv-none">暂无上报</span>
            </li>
            <li>
              <span class="asv-meter-label"><MemoryStick aria-hidden="true" />内存</span>
              <template v-if="current && memPercent !== null"><b>{{ bytes(current.mem_used) }} / {{ bytes(current.mem_total) }}</b><span class="host-bar" aria-hidden="true"><span :style="bar(memPercent)" /></span></template>
              <span v-else class="asv-none">暂无上报</span>
            </li>
          </ul>
          <div class="asv-deploy-link">
            <RouterLink v-if="server.kind === 'ssh' && server.deployable" class="asv-go" :to="{ path: '/agents', query: { deploy: '1', server_id: server.id } }">前往部署智能体<ArrowRight aria-hidden="true" /></RouterLink>
            <p v-else-if="server.kind === 'ssh'" class="form-help">暂不能部署：{{ blockReason(server) }}</p>
            <p v-else class="form-help">自动识别的服务器不能自动部署。</p>
          </div>
        </div>

        <dl class="asv-head-facts">
          <div><dt>连接状态</dt><dd><span v-if="connection" class="asv-state" :class="`tone-${connection.tone}`">{{ connection.label }}</span></dd></div>
          <div><dt>环境状态</dt><dd>{{ init?.label }}</dd></div>
          <div><dt>最近检查</dt><dd>{{ server.ssh?.last_connection_at ? formatTime(server.ssh.last_connection_at) : '—' }}</dd></div>
          <div><dt>最近上报</dt><dd>{{ server.reported_at ? formatTime(server.reported_at) : '暂无上报' }}<template v-if="server.reported_at && server.stale">（已过期）</template></dd></div>
        </dl>
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
