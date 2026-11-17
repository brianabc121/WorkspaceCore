<script setup lang="ts">
import { computed, ref } from 'vue'
import { Box, Cpu, HardDrive, Info, Layers, MemoryStick, Plug, Server as ServerIcon, ShieldCheck, X } from '@lucide/vue'
import type { Schema } from '../../services/api'
import { formatTime } from '../time'
import { HOST_STATUS, type EnvFilters, type RunnerHost } from './api'
import { ago, binary } from './containerMetrics'
import { HOST_TONE, INGRESS, REASON, hostAlerts, hostMetrics, type Candidate } from './hosts'
import OverviewTab from './OverviewTab.vue'
import RunnerDeployPanel from './RunnerDeployPanel.vue'
import RunnerPolicy from './RunnerPolicy.vue'
import ModuleServerPanels from '../../modules/ModuleServerPanels.vue'

// 容器服务器详情（T-0277，按智能体服务器的详情排版）：页头卡片——名称、状态、版本与入口，一行六格（连接、运行环境、CPU、内存、磁盘、容器），
// 报告时间与磁盘构成、告警；连接与部署；这台服务器上的容器（按 host_id 筛选，服务端分页）；
// 插拔式模块的区块（例如大模型客户端的“MCP 管理器”，T-0234，经 ModuleServerPanels 渲染，这里不导入模块的代码）；回收策略。
// 读数来自 runner-agent 的完整报告（大约每分钟一次）；读不到就写原因，不画 0。
const props = defineProps<{ host: RunnerHost; candidate: Candidate | null; candidatesFailed: boolean; instances: Schema['RunnerInstanceUsage'][]; selectedEnv: string | null; reload: number }>()
const emit = defineEmits<{ close: []; removed: []; select: [id: string, row: HTMLElement | null] }>()

const m = computed(() => hostMetrics(props.host))
const alerts = computed(() => hostAlerts(props.host))
const tone = computed(() => HOST_TONE[props.host.status])
const pending = computed(() => props.host.status === 'pending')
const percent = (ratio: number) => `${Math.round(ratio * 100)}%`
const bar = (ratio: number) => ({ width: `${Math.min(100, Math.max(0, ratio * 100))}%` })
const loadRatio = computed(() => (m.value.load !== null && props.host.cpus ? m.value.load / props.host.cpus : null))
const policy = computed(() => props.host.policy ?? {})
const diskDanger = computed(() => m.value.diskRatio > (policy.value.disk_high ?? 0.8))
const memDanger = computed(() => m.value.memRatio > (policy.value.mem_high ?? 0.85))
const facts = computed(() => [
  props.host.backend === 'kubernetes' ? `Kubernetes${props.host.node_name ? ` 节点 ${props.host.node_name}` : ''}` : props.host.docker_version ? `Docker ${props.host.docker_version}` : 'Docker',
  props.host.agent_version ? `runner-agent ${props.host.agent_version}` : '',
  INGRESS[props.host.ingress],
].filter(Boolean).join(' · '))
// 没有读数时写清楚原因
const empty = computed(() => pending.value ? { value: '部署后显示', note: 'runner-agent 连上后每分钟报告一次' } : { value: '暂无报告', note: m.value.reportAt ? '' : 'runner-agent 还没有完整报告' })
const reportNote = computed(() => {
  if (pending.value) return '部署完成、runner-agent 连上工作台后，每分钟报告一次 CPU、内存、磁盘与容器。'
  const at = m.value.reportAt
  if (!at) return 'runner-agent 还没有完整报告：读数要等它报告一次（大约每分钟一次）。'
  const parts = [`runner-agent 大约每分钟报告一次 · 最近完整报告 ${formatTime(at)}${props.host.status === 'online' ? '' : '（已过期）'}`]
  if (m.value.imagesBytes !== null || m.value.volumesBytes !== null) parts.push(`磁盘里：镜像 ${binary(m.value.imagesBytes)} · 容器可写层 ${binary(m.value.containersBytes)} · 数据卷 ${binary(m.value.volumesBytes)}`)
  return parts.join('；')
})
// 自动分配：这台服务器能不能接新环境
const placement = computed(() => {
  const c = props.candidate
  if (!c) return props.candidatesFailed ? '自动分配状态刷新失败' : pending.value ? '部署后参与自动分配' : '暂无调度采样'
  return c.eligible ? '可参与自动分配' : `暂不可分配：${c.reasons.map(r => REASON[r] ?? r).join('、') || '原因未知'}`
})

// 这台服务器上的容器：筛选只在详情里（不写进地址，换服务器就清掉）
const filters = ref<EnvFilters>({})
</script>

<template>
  <div class="asv-detail">
    <section class="ws-panel asv-head" :aria-label="`容器服务器：${host.name}`">
      <div class="asv-head-top">
        <span class="asv-icon" aria-hidden="true"><ServerIcon /></span>
        <div class="asv-head-title">
          <h2>{{ host.name }} <span class="asv-state" :class="`tone-${tone}`">{{ HOST_STATUS[host.status] }}</span><span v-if="host.backend === 'kubernetes'" class="badge">Kubernetes</span></h2>
          <p class="asv-sub">{{ facts }}</p>
        </div>
        <div class="asv-head-actions">
          <button type="button" class="icon-button asv-close" aria-label="关闭服务器详情" @click="emit('close')"><X aria-hidden="true" /></button>
        </div>
      </div>

      <ul class="asv-stats" aria-label="服务器概览">
        <li>
          <span class="asv-stat-label"><Plug aria-hidden="true" />连接</span>
          <span class="asv-stat-value"><span class="asv-state" :class="`tone-${tone}`">{{ HOST_STATUS[host.status] }}</span></span>
          <span class="asv-stat-note">{{ host.last_seen_at ? `最近联系 ${ago(host.last_seen_at)}` : '还没有连上工作台' }}</span>
        </li>
        <li>
          <span class="asv-stat-label"><ShieldCheck aria-hidden="true" />运行环境</span>
          <template v-if="host.runtimes.length">
            <span class="asv-stat-value"><span class="asv-state" :class="m.gvisor ? 'tone-green' : 'tone-amber'">{{ m.gvisor ? 'gVisor 可用' : '没装 gVisor' }}</span></span>
            <span class="asv-stat-note">运行时 {{ host.runtimes.join('、') }}{{ host.kvm ? ' · 有 /dev/kvm' : '' }}</span>
          </template>
          <template v-else><span class="asv-stat-value asv-none">{{ empty.value }}</span><span class="asv-stat-note">部署时安装 Docker 与 gVisor</span></template>
        </li>
        <li class="asv-stat-usage">
          <span class="asv-stat-label"><Cpu aria-hidden="true" />CPU</span>
          <template v-if="host.cpus">
            <span class="asv-stat-value"><b>{{ host.cpus }} 核</b><span v-if="loadRatio !== null" class="asv-stat-sub">负载 {{ percent(loadRatio) }}</span></span>
            <span v-if="loadRatio !== null" class="host-bar" aria-hidden="true"><span :style="bar(loadRatio)" /></span>
            <span class="asv-stat-note">{{ m.load !== null ? `1 分钟负载 ${m.load.toFixed(2)}` : '负载暂无报告' }}</span>
          </template>
          <template v-else><span class="asv-stat-value asv-none">{{ empty.value }}</span><span v-if="empty.note" class="asv-stat-note">{{ empty.note }}</span></template>
        </li>
        <li class="asv-stat-usage" :class="{ 'is-danger': memDanger }">
          <span class="asv-stat-label"><MemoryStick aria-hidden="true" />内存</span>
          <template v-if="m.memTotal && m.memUsed !== null">
            <b class="asv-stat-value">{{ binary(m.memUsed) }} / {{ binary(m.memTotal) }}</b>
            <span class="host-bar" aria-hidden="true"><span :style="bar(m.memRatio)" /></span>
            <span class="asv-stat-note">已用 {{ percent(m.memRatio) }} · 可用 {{ binary(m.memFree) }}</span>
          </template>
          <template v-else-if="m.memTotal"><b class="asv-stat-value">{{ binary(m.memTotal) }}</b><span class="asv-stat-note">已用暂无报告</span></template>
          <template v-else><span class="asv-stat-value asv-none">{{ empty.value }}</span><span v-if="empty.note" class="asv-stat-note">{{ empty.note }}</span></template>
        </li>
        <li class="asv-stat-usage" :class="{ 'is-danger': diskDanger }">
          <span class="asv-stat-label"><HardDrive aria-hidden="true" />磁盘</span>
          <template v-if="m.diskTotal && m.diskUsed !== null">
            <b class="asv-stat-value" title="主机磁盘：已用 = 总量 − 可用；与各容器的数据卷占用是不同的口径">{{ binary(m.diskUsed) }} / {{ binary(m.diskTotal) }}</b>
            <span class="host-bar" aria-hidden="true"><span :style="bar(m.diskRatio)" /></span>
            <span class="asv-stat-note">已用 {{ percent(m.diskRatio) }} · 可用 {{ binary(m.diskFree) }}</span>
          </template>
          <template v-else><span class="asv-stat-value asv-none">{{ empty.value }}</span><span v-if="empty.note" class="asv-stat-note">{{ empty.note }}</span></template>
        </li>
        <li>
          <span class="asv-stat-label"><Box aria-hidden="true" />容器</span>
          <template v-if="m.containers !== null">
            <span class="asv-stat-value">{{ m.containers }} 个<span v-if="m.managers" class="asv-stat-sub">另有 {{ m.managers }} 个 MCP 管理器</span></span>
            <span class="asv-stat-note">{{ placement }}</span>
          </template>
          <template v-else><span class="asv-stat-value asv-none">{{ empty.value }}</span><span class="asv-stat-note">{{ placement }}</span></template>
        </li>
      </ul>
      <div class="asv-res" role="group" aria-label="服务器读数">
        <div class="asv-res-head">
          <p class="asv-res-note" :class="{ 'is-warn': host.status === 'offline' }"><Info aria-hidden="true" />{{ reportNote }}</p>
        </div>
        <dl v-if="candidate && !pending" class="cs-placement" aria-label="自动分配">
          <div><dt><Layers aria-hidden="true" />已承诺 / 创建预约</dt><dd>{{ candidate.committed_cpus }} 核 · {{ candidate.committed_memory_mb }} MiB · {{ candidate.committed_disk_gb }} GB</dd></div>
          <div><dt>调度可用</dt><dd>内存 {{ candidate.available_memory_mb === null ? '—' : binary(candidate.available_memory_mb * 1024 ** 2) }} · 磁盘 {{ candidate.disk_free_gb ?? '—' }} GB</dd></div>
          <div><dt>资源采样</dt><dd>{{ candidate.observed_at ? formatTime(candidate.observed_at) : '未知（不能按空闲处理）' }}</dd></div>
        </dl>
      </div>
      <ul v-if="alerts.length" class="host-alerts" aria-label="正在提醒的告警">
        <li v-for="a in alerts" :key="a"><span class="host-alert-icon" aria-hidden="true">!</span>{{ a }}</li>
      </ul>
    </section>

    <RunnerDeployPanel :key="host.id" :host="host" @removed="emit('removed')" />

    <OverviewTab
      :key="`envs:${host.id}`" title="这台服务器上的容器" :host-id="host.id" :instances="instances" :filters="filters" :workspace-total="null"
      :selected-id="selectedEnv" :reload="reload" @filters="filters = $event" @select="(id, row) => emit('select', id, row)"
    />

    <ModuleServerPanels place="runnerHost" :server-id="host.id" :server-name="host.name" />

    <RunnerPolicy :key="`policy:${host.id}`" :host="host" />
  </div>
</template>
