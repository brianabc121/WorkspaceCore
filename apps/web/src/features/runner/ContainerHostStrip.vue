<script setup lang="ts">
import { computed } from 'vue'
import { ChevronRight, Cpu, HardDrive, Server, ShieldCheck } from '@lucide/vue'
import type { Schema } from '../../services/api'
import { formatTime } from '../time'
import { HOST_STATUS } from './api'
import { ago, binary } from './containerMetrics'

// 容器服务器页的主机条（T-0186，效果图顶部那一行）：名称与在线状态、CPU 核数 / 内存、主机磁盘已用 / 总量、
// 运行时（gVisor 是否可用）、最近联系与最近一次完整报告。“最近联系”是长轮询的心跳（last_seen_at），
// “完整报告”是对账上报的时间（report.at）——容器读数跟着报告走，不把心跳说成读数更新。
// 告警（离线、磁盘 / 内存超过水位、有不是工作台建的容器、没装 gVisor）沿用原来总览页的判断。
const props = defineProps<{ host: Schema['RunnerHostOut'] }>()
defineEmits<{ settings: [] }>()
const report = computed(() => (props.host.report ?? {}) as Record<string, number | string | null | undefined>)
const num = (value: unknown) => (typeof value === 'number' && Number.isFinite(value) ? value : null)
const policy = computed(() => props.host.policy ?? {})
const memTotal = computed(() => num(report.value.mem_bytes) || props.host.mem_bytes || 0)
const memUsed = computed(() => (memTotal.value && num(report.value.mem_free_bytes) !== null ? memTotal.value - (num(report.value.mem_free_bytes) ?? 0) : null))
const diskTotal = computed(() => num(report.value.disk_bytes) || props.host.disk_bytes || 0)
const diskFree = computed(() => num(report.value.disk_free_bytes) ?? props.host.disk_free_bytes)
const diskUsed = computed(() => (diskTotal.value && diskFree.value !== null && diskFree.value !== undefined ? diskTotal.value - diskFree.value : null))
const ratio = (used: number | null, total: number) => (used !== null && total ? Math.min(1, Math.max(0, used / total)) : 0)
const gvisor = computed(() => props.host.runtimes.includes('runsc'))
const reportAt = computed(() => (typeof report.value.at === 'string' ? report.value.at : null))
const tone = computed(() => ({ online: 'green', offline: 'red', pending: 'amber', disabled: 'muted' } as const)[props.host.status])
const alerts = computed(() => {
  const out: string[] = []
  const host = props.host
  if (host.status === 'offline') out.push(`容器服务器离线：最近一次联系 ${host.last_seen_at ? formatTime(host.last_seen_at) : '从未'}。环境不会被删除，回到在线后继续；离线期间的读数都按“已过期”显示。`)
  if (host.status === 'disabled') out.push('容器服务器已停用：智能体不能新建或使用环境，已有读数按“已过期”显示。')
  const disk = ratio(diskUsed.value, diskTotal.value)
  if (disk > (policy.value.disk_emergency ?? 0.95)) out.push(`主机磁盘告急（已用 ${Math.round(disk * 100)}%）：暂停新建环境，一次性运行已停止。`)
  else if (disk > (policy.value.disk_high ?? 0.8)) out.push(`主机磁盘超过高水位（已用 ${Math.round(disk * 100)}%）：正在按保留分归档停止的环境、清理没人用的镜像。`)
  if (ratio(memUsed.value, memTotal.value) > (policy.value.mem_high ?? 0.85)) out.push('主机内存超过高水位：正在按保留分停止空闲的环境。')
  if (Number(report.value.unlabeled_containers ?? 0) > 0) out.push(`服务器上有 ${report.value.unlabeled_containers} 个不是工作台建的容器：容器服务器最好专用，工作台不会碰它们。`)
  if (!gvisor.value) out.push('没装 gVisor（runsc）：智能体只能选 runc。重新部署容器服务器会装上。')
  return out
})
</script>

<template>
  <section class="ws-panel cs-host" aria-label="容器服务器主机">
    <div class="cs-host-row">
      <span class="cs-host-name"><Server aria-hidden="true" /><strong>{{ host.name }}</strong></span>
      <span class="asv-state" :class="`tone-${tone}`">{{ HOST_STATUS[host.status] }}</span>
      <span class="cs-host-fact"><Cpu aria-hidden="true" />{{ host.cpus ?? '—' }} 核 / {{ memTotal ? binary(memTotal) : '—' }}</span>
      <span class="cs-host-fact" :title="'主机磁盘：已用 = 总量 − 可用；与各容器的数据卷占用是不同的口径'"><HardDrive aria-hidden="true" />磁盘 {{ diskUsed !== null ? binary(diskUsed) : '—' }} / {{ diskTotal ? binary(diskTotal) : '—' }}</span>
      <span class="cs-host-fact"><ShieldCheck aria-hidden="true" />gVisor {{ gvisor ? '可用' : '未安装' }}</span>
      <span class="cs-host-fact cs-muted">最近联系 {{ host.last_seen_at ? ago(host.last_seen_at) : '从未' }}<template v-if="reportAt"> · 完整报告 {{ ago(reportAt) }}</template></span>
      <button type="button" class="asv-link cs-host-link" @click="$emit('settings')">服务器设置<ChevronRight aria-hidden="true" /></button>
    </div>
    <details class="cs-host-more">
      <summary>主机详情</summary>
      <dl class="agent-facts">
        <div><dt>runner-agent</dt><dd>{{ host.agent_version ?? '—' }}</dd></div>
        <div><dt>Docker</dt><dd>{{ host.docker_version ?? '—' }}</dd></div>
        <div><dt>运行时</dt><dd>{{ host.runtimes.length ? host.runtimes.join('、') : '—' }}</dd></div>
        <div><dt>/dev/kvm</dt><dd>{{ host.kvm ? '有（可以跑安卓模拟器等）' : '没有' }}</dd></div>
        <div><dt>负载</dt><dd>{{ num(report.load) !== null ? Number(report.load).toFixed(2) : '—' }}</dd></div>
        <div><dt>内存</dt><dd>{{ memUsed !== null ? binary(memUsed) : '—' }} / {{ memTotal ? binary(memTotal) : '—' }}</dd></div>
        <div><dt>磁盘里</dt><dd>镜像 {{ binary(num(report.images_bytes)) }} · 容器可写层 {{ binary(num(report.containers_bytes)) }} · 数据卷 {{ binary(num(report.volumes_bytes)) }}</dd></div>
        <div><dt>最近一次完整报告</dt><dd>{{ reportAt ? formatTime(reportAt) : '还没有' }}</dd></div>
      </dl>
    </details>
    <ul v-if="alerts.length" class="host-alerts" aria-label="告警"><li v-for="a in alerts" :key="a">{{ a }}</li></ul>
  </section>
</template>
