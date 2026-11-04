<script setup lang="ts">
import { computed, ref } from 'vue'
import { useAuthStore } from '../../stores/auth'
import type { Schema } from '../../services/api'
import { formatTime } from '../time'
import { agentError } from './queries'
import { ALERT, DISK_USED_MAX, MEMORY_USED_MAX, RANGE, bytes, duration, load, percent, rate, useHostStatus, type HostRange } from './host'
import HostChart from './HostChart.vue'

// 实例卡片里的“服务器状态”（T-0113，AGENT_OPS_PLAN 5.3）：小指示一直显示（来自 host-summary，一次请求取全部实例）；
// 展开后才取这个实例的当前值和趋势，每分钟刷新。数值是整台服务器的。
const props = defineProps<{ instance: Schema['AgentInstanceOut']; host?: Schema['HostSummaryItem'] }>()
const auth = useAuthStore(), scope = auth.workspaceId!
const open = ref(false), range = ref<HostRange>('24h')
const status = useHostStatus(scope, () => props.instance.id, () => range.value, () => open.value)
const data = computed(() => status.data.value?.instance_id === props.instance.id ? status.data.value : undefined)
const current = computed(() => data.value?.current ?? null)
const alerts = computed(() => data.value?.alerts ?? props.host?.alerts ?? [])

const meters = computed(() => {
  const h = props.host
  if (!h?.reported_at) return []
  return [
    { key: 'cpu', label: 'CPU', value: h.cpu_percent, danger: h.alerts.includes('load_high'), warn: '负载偏高' },
    { key: 'mem', label: '内存', value: h.mem_used_percent, danger: h.alerts.includes('memory_low') || (h.mem_used_percent ?? 0) >= MEMORY_USED_MAX, warn: '可用不足' },
    { key: 'disk', label: '磁盘', value: h.disk_used_percent, danger: h.alerts.includes('disk_low') || (h.disk_used_percent ?? 0) >= DISK_USED_MAX, warn: '空间不足' },
  ]
})
const memUsedPercent = computed(() => {
  const c = current.value
  return c?.mem_total && c.mem_used !== null ? (100 * c.mem_used) / c.mem_total : null
})
const disks = computed(() => {
  const c = current.value
  if (!c) return []
  return [
    { key: 'data', label: '接入包目录所在分区', help: 'OpenClaw 的会话和记忆在这里', disk: c.data_disk },
    { key: 'root', label: 'Docker 数据目录所在分区', help: '镜像在这里', disk: c.root_disk },
  ]
})
const bar = (value: number | null | undefined) => ({ width: `${Math.min(100, Math.max(0, value ?? 0))}%` })
</script>

<template>
  <section class="agent-section host-status" aria-label="服务器状态">
    <div class="section-heading">
      <h3>服务器状态 <span v-if="host?.reported_at && host.stale" class="badge">数据过期</span></h3>
      <button type="button" class="secondary-button" :aria-expanded="open" @click="open = !open">{{ open ? '收起服务器状态' : '查看服务器状态' }}</button>
    </div>
    <ul v-if="meters.length" class="host-meters" aria-label="当前使用率">
      <li v-for="m in meters" :key="m.key" :class="{ 'is-danger': m.danger }">
        <span>{{ m.label }} <b>{{ percent(m.value) }}</b><template v-if="m.danger"> · {{ m.warn }}</template></span>
        <span class="host-bar" aria-hidden="true"><span :style="bar(m.value)" /></span>
      </li>
    </ul>
    <p v-else-if="host && !open" class="form-help">还没有收到这台服务器的状态。</p>
    <div v-if="open" class="host-detail">
      <p v-if="status.isPending.value" role="status">正在加载服务器状态…</p>
      <p v-else-if="status.error.value" role="alert">{{ agentError(status.error.value) }}</p>
      <template v-else-if="data">
        <p v-if="!data.reported" class="form-help">还没有收到这台服务器的状态。用上面的 SSH 自动部署会写好每分钟上报的定时任务；手动接入的，按接入包 CHECKLIST.md 在 crontab 里加一行。旧接入包重新部署后才有。</p>
        <template v-else>
          <ul v-if="alerts.length" class="host-alerts" aria-label="正在提醒的告警">
            <li v-for="a in alerts" :key="a"><span class="host-alert-icon" aria-hidden="true">!</span>{{ ALERT[a] }}</li>
          </ul>
          <div v-if="current" class="host-cards">
            <section class="host-card" aria-label="CPU">
              <h4>CPU</h4>
              <p class="host-big">{{ percent(current.cpu_percent) }}</p>
              <p class="form-help">{{ current.cores ?? '—' }} 核<template v-if="current.cpu_model"> · {{ current.cpu_model }}</template></p>
              <p class="form-help">负载 {{ current.load ? current.load.map(load).join(' / ') : '—' }}（1 / 5 / 15 分钟）</p>
              <p v-if="current.cpu_percent === null" class="form-help">使用率要两次上报才算得出，下一分钟显示。</p>
            </section>
            <section class="host-card" aria-label="内存" :class="{ 'is-danger': alerts.includes('memory_low') }">
              <h4>内存</h4>
              <p class="host-big">{{ bytes(current.mem_used) }} <span class="form-help">/ {{ bytes(current.mem_total) }}</span></p>
              <span class="host-bar" aria-hidden="true"><span :style="bar(memUsedPercent)" /></span>
              <p class="form-help host-usage">已用 {{ percent(memUsedPercent) }} · 可用 {{ bytes(current.mem_available) }}<template v-if="alerts.includes('memory_low')"> · 可用不足</template></p>
              <p v-if="current.swap_total" class="form-help">交换 {{ bytes(current.swap_used) }} / {{ bytes(current.swap_total) }}</p>
              <p v-if="current.mem_limit" class="form-help">受容器限制：OpenClaw 容器最多用 {{ bytes(current.mem_limit) }}</p>
            </section>
            <section v-for="d in disks" :key="d.key" class="host-card" :aria-label="d.label" :class="{ 'is-danger': d.disk && d.disk.used_percent >= DISK_USED_MAX }">
              <h4>{{ d.label }}</h4>
              <template v-if="d.disk">
                <p class="host-big">{{ bytes(d.disk.used) }} <span class="form-help">/ {{ bytes(d.disk.total) }}</span></p>
                <span class="host-bar" aria-hidden="true"><span :style="bar(d.disk.used_percent)" /></span>
                <p class="form-help host-usage">已用 {{ percent(d.disk.used_percent) }} · 可用 {{ bytes(d.disk.free) }}<template v-if="d.disk.used_percent >= DISK_USED_MAX"> · 空间不足</template></p>
              </template>
              <p v-else class="form-help">读不到这个分区。</p>
              <p class="form-help">{{ d.help }}</p>
            </section>
            <section class="host-card" aria-label="网络">
              <h4>网络</h4>
              <p class="form-help">下载 <b>{{ rate(current.rx_rate) }}</b></p>
              <p class="form-help">上传 <b>{{ rate(current.tx_rate) }}</b></p>
              <p v-if="current.rx_rate === null" class="form-help">速率要两次上报才算得出。</p>
            </section>
            <section class="host-card" aria-label="开机时长与上报">
              <h4>开机时长</h4>
              <p class="host-big">{{ duration(current.uptime_seconds) }}</p>
              <p class="form-help">最后上报 {{ formatTime(current.reported_at) }}<template v-if="current.stale"> · 数据过期（超过 3 分钟没有上报）</template></p>
            </section>
          </div>
          <div class="segmented host-range" role="group" aria-label="趋势的时间范围">
            <button v-for="(label, key) in RANGE" :key="key" type="button" :aria-pressed="range === key" @click="range = key">最近 {{ label }}</button>
          </div>
          <div class="host-charts">
            <HostChart title="CPU 使用率" kind="percent" :range="range" :points="data.points" :series="[{ key: 'cpu_avg', label: '平均' }, { key: 'cpu_max', label: '峰值' }]" />
            <HostChart title="负载（1 分钟）" :note="current?.cores ? `${current.cores} 核` : undefined" kind="load" :range="range" :points="data.points" :series="[{ key: 'load_avg', label: '平均' }, { key: 'load_max', label: '峰值' }]" />
            <HostChart title="内存已用" kind="percent" :range="range" :points="data.points" :series="[{ key: 'mem_avg', label: '平均' }, { key: 'mem_max', label: '峰值' }]" />
            <HostChart title="磁盘已用" note="每小时平均" kind="percent" :range="range" :points="data.points" :series="[{ key: 'data_avg', label: '接入包目录' }, { key: 'root_avg', label: 'Docker 数据目录' }]" />
            <HostChart title="网络" note="每小时平均，悬停看峰值" kind="rate" :range="range" :points="data.points" :series="[{ key: 'rx_avg', label: '下载' }, { key: 'tx_avg', label: '上传' }]" :extra="[{ key: 'rx_max', label: '下载峰值' }, { key: 'tx_max', label: '上传峰值' }]" />
          </div>
        </template>
      </template>
    </div>
  </section>
</template>
