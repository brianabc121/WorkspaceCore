<script setup lang="ts">
import { computed, ref } from 'vue'
import { useAuthStore } from '../../stores/auth'
import type { Schema } from '../../services/api'
import { formatTime } from '../time'
import { agentError } from './queries'
import { ALERT, DISK_USED_MAX, MEMORY_USED_MAX, RANGE, bytes, percent, rate, useHostStatus, type HostRange } from './host'
import { AGENT_ALERT } from './servers'
import HostCards from './HostCards.vue'
import HostTrends from './HostTrends.vue'
import HostChart from './HostChart.vue'

// 实例卡片里的“负载”（T-0113，一台服务器多个实例后改为 T-0147）：小指示一直显示（来自 host-summary，一次请求取全部实例）——
// 先是这个智能体自己的 CPU / 内存，再是它所在服务器的整机 CPU / 内存 / 磁盘。展开后才取当前值和趋势，每分钟刷新。
const props = defineProps<{ instance: Schema['AgentInstanceOut']; host?: Schema['HostSummaryItem']; serverName?: string }>()
const auth = useAuthStore(), scope = auth.workspaceId!
const open = ref(false), range = ref<HostRange>('24h')
const status = useHostStatus(scope, () => props.instance.id, () => range.value, () => open.value)
const data = computed(() => status.data.value?.instance_id === props.instance.id ? status.data.value : undefined)
const current = computed(() => data.value?.current ?? null)
const alerts = computed(() => data.value?.alerts ?? props.host?.alerts ?? [])
const agentAlerts = computed(() => data.value?.agent_alerts ?? props.host?.agent_alerts ?? [])
const agent = computed(() => data.value?.agent ?? null)
const serverId = computed(() => data.value?.server?.id ?? props.host?.server_id ?? props.instance.server_id ?? null)
const server = computed(() => data.value?.server?.name ?? props.serverName ?? null)

const own = computed(() => {
  const h = props.host
  if (!h?.reported_at) return []
  const memTotal = h.agent_mem_limit
  const memText = h.agent_mem_bytes === null ? '无法获取' : `${bytes(h.agent_mem_bytes)}${memTotal ? ` / ${bytes(memTotal)}` : ''}`
  return [
    { key: 'agent-cpu', label: '它的 CPU', text: h.agent_cpu_percent === null ? '无法获取' : percent(h.agent_cpu_percent), value: h.agent_cpu_percent, danger: h.agent_alerts.includes('cpu_hog'), warn: '长时间占满' },
    { key: 'agent-mem', label: '它的内存', text: memText, value: memTotal && h.agent_mem_bytes !== null ? (100 * h.agent_mem_bytes) / memTotal : null, danger: h.agent_alerts.includes('mem_near_limit'), warn: '接近上限' },
  ]
})
const meters = computed(() => {
  const h = props.host
  if (!h?.reported_at) return []
  return [
    { key: 'cpu', label: '整机 CPU', value: h.cpu_percent, danger: h.alerts.includes('load_high'), warn: '负载偏高' },
    { key: 'mem', label: '整机内存', value: h.mem_used_percent, danger: h.alerts.includes('memory_low') || (h.mem_used_percent ?? 0) >= MEMORY_USED_MAX, warn: '可用不足' },
    { key: 'disk', label: '整机磁盘', value: h.disk_used_percent, danger: h.alerts.includes('disk_low') || (h.disk_used_percent ?? 0) >= DISK_USED_MAX, warn: '空间不足' },
  ]
})
const bar = (value: number | null | undefined) => ({ width: `${Math.min(100, Math.max(0, value ?? 0))}%` })
</script>

<template>
  <section class="agent-section host-status" aria-label="负载">
    <div class="section-heading">
      <h3>负载 <span v-if="host?.reported_at && host.stale" class="badge">数据过期</span></h3>
      <button type="button" class="secondary-button" :aria-expanded="open" @click="open = !open">{{ open ? '收起负载' : '查看负载' }}</button>
    </div>
    <p class="form-help">
      所在服务器：<RouterLink v-if="serverId && server" :to="{ path: '/agent-servers', query: { server: serverId } }">{{ server }}</RouterLink><template v-else>还没有识别（第一次上报后自动归到服务器）</template>
    </p>
    <ul v-if="own.length" class="host-meters" aria-label="这个智能体的当前用量">
      <li v-for="m in own" :key="m.key" :class="{ 'is-danger': m.danger }">
        <span>{{ m.label }} <b>{{ m.text }}</b><template v-if="m.danger"> · {{ m.warn }}</template></span>
        <span class="host-bar" aria-hidden="true"><span :style="bar(m.value)" /></span>
      </li>
    </ul>
    <ul v-if="meters.length" class="host-meters" aria-label="整台服务器的当前使用率">
      <li v-for="m in meters" :key="m.key" :class="{ 'is-danger': m.danger }">
        <span>{{ m.label }} <b>{{ percent(m.value) }}</b><template v-if="m.danger"> · {{ m.warn }}</template></span>
        <span class="host-bar" aria-hidden="true"><span :style="bar(m.value)" /></span>
      </li>
    </ul>
    <p v-else-if="host && !open" class="form-help">还没有收到这个实例的上报。</p>
    <div v-if="open" class="host-detail">
      <p v-if="status.isPending.value" role="status">正在加载负载…</p>
      <p v-else-if="status.error.value" role="alert">{{ agentError(status.error.value) }}</p>
      <template v-else-if="data">
        <p v-if="!data.reported" class="form-help">还没有收到这个实例的上报。部署到服务器会写好每分钟上报的定时任务；手动接入的，按接入包 CHECKLIST.md 在 crontab 里加一行。旧接入包重新部署后才有。</p>
        <template v-else>
          <ul v-if="alerts.length || agentAlerts.length" class="host-alerts" aria-label="正在提醒的告警">
            <li v-for="a in agentAlerts" :key="a"><span class="host-alert-icon" aria-hidden="true">!</span>这个智能体：{{ AGENT_ALERT[a] }}</li>
            <li v-for="a in alerts" :key="a"><span class="host-alert-icon" aria-hidden="true">!</span>整台服务器：{{ ALERT[a] }}</li>
          </ul>
          <h4>这个智能体</h4>
          <p v-if="!agent" class="form-help">还没有收到这个智能体自己的用量（接入包较旧时只有整机数据，重新部署后才有）。</p>
          <p v-else-if="!agent.available" class="form-help">无法获取这个智能体自己的用量（服务器上读不到它的 cgroup）。</p>
          <div v-else class="host-cards">
            <section class="host-card" aria-label="它的 CPU">
              <h4>CPU（占整机）</h4>
              <p class="host-big">{{ percent(agent.cpu_percent) }}</p>
              <p class="form-help">{{ agent.cpu_cores === null ? '—' : agent.cpu_cores.toFixed(2) }} 核 · 进程 {{ agent.pids ?? '—' }}</p>
            </section>
            <section class="host-card" aria-label="它的内存" :class="{ 'is-danger': agentAlerts.includes('mem_near_limit') }">
              <h4>内存</h4>
              <p class="host-big">{{ bytes(agent.mem_bytes) }} <span class="form-help">/ {{ agent.mem_limit ? `上限 ${bytes(agent.mem_limit)}` : `整机 ${bytes(agent.mem_total)}` }}</span></p>
              <p class="form-help host-usage">{{ agent.mem_limit ? '设了内存上限' : '没设内存上限（在“资源上限”里设置）' }}</p>
            </section>
            <section class="host-card" aria-label="它的数据与网络">
              <h4>数据目录与网络</h4>
              <p class="form-help">数据目录 <b>{{ agent.data_bytes === null ? '—' : `${agent.data_truncated ? '≥ ' : ''}${bytes(agent.data_bytes)}` }}</b></p>
              <p class="form-help">下载 <b>{{ rate(agent.rx_rate) }}</b> · 上传 <b>{{ rate(agent.tx_rate) }}</b></p>
              <p class="form-help">最后上报 {{ formatTime(agent.reported_at) }}<template v-if="agent.restarts_1h"> · 1 小时重启 {{ agent.restarts_1h }} 次</template></p>
            </section>
          </div>
          <h4>所在服务器{{ server ? `：${server}` : '' }}</h4>
          <HostCards v-if="current" :current="current" :alerts="alerts" />
          <div class="segmented host-range" role="group" aria-label="趋势的时间范围">
            <button v-for="(label, key) in RANGE" :key="key" type="button" :aria-pressed="range === key" @click="range = key">最近 {{ label }}</button>
          </div>
          <div class="host-charts">
            <HostChart title="它的 CPU（占整机）" kind="percent" :range="range" :points="data.agent_points" :series="[{ key: 'cpu_avg', label: '平均' }, { key: 'cpu_max', label: '峰值' }]" />
            <HostChart title="它的内存" kind="bytes" :range="range" :points="data.agent_points" :series="[{ key: 'mem_avg', label: '平均' }, { key: 'mem_max', label: '峰值' }]" />
          </div>
          <HostTrends :points="data.points" :range="range" :cores="current?.cores" />
        </template>
      </template>
    </div>
  </section>
</template>
