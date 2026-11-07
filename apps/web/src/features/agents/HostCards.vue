<script setup lang="ts">
import { computed } from 'vue'
import type { Schema } from '../../services/api'
import { formatTime } from '../time'
import { DISK_USED_MAX, bytes, duration, load, percent, rate, type HostAlert } from './host'

// 整台服务器的当前值（T-0113；实例卡片与服务器详情共用，T-0147）：CPU、内存、两个分区、网络、开机时长。
const props = defineProps<{ current: Schema['HostCurrent']; alerts: HostAlert[] }>()
const memUsedPercent = computed(() => {
  const c = props.current
  return c.mem_total && c.mem_used !== null ? (100 * c.mem_used) / c.mem_total : null
})
const disks = computed(() => [
  { key: 'data', label: '接入包目录所在分区', help: 'OpenClaw 的会话和记忆在这里', disk: props.current.data_disk },
  { key: 'root', label: 'Docker 数据目录所在分区', help: '镜像在这里', disk: props.current.root_disk },
])
const bar = (value: number | null | undefined) => ({ width: `${Math.min(100, Math.max(0, value ?? 0))}%` })
</script>

<template>
  <div class="host-cards">
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
</template>
