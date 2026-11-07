<script setup lang="ts">
import { computed } from 'vue'
import type { Schema } from '../../services/api'
import { formatTime } from '../time'
import { HOST_STATUS, LIFETIME, STATE, bytes } from './api'

// 总览（RUNNER_PLAN 第 11 节）：服务器状态与版本、CPU / 内存 / 磁盘（磁盘分镜像 / 容器 / 数据卷）、
// 按状态的环境数、每个智能体的用量与限额、告警、最近的回收记录。对账每分钟自动做，镜像在磁盘超过高水位时自动清理。
const props = defineProps<{ overview: Schema['RunnerOverview'] }>()
defineEmits<{ environments: [state?: string]; settings: [] }>()
const host = computed(() => props.overview.host)
const report = computed(() => (host.value?.report ?? {}) as Record<string, number | null | undefined>)
const policy = computed(() => host.value?.policy ?? {})
const memTotal = computed(() => Number(report.value.mem_bytes || host.value?.mem_bytes || 0))
const memUsed = computed(() => memTotal.value ? memTotal.value - Number(report.value.mem_free_bytes ?? memTotal.value) : 0)
const diskTotal = computed(() => Number(report.value.disk_bytes || host.value?.disk_bytes || 0))
const diskUsed = computed(() => diskTotal.value ? diskTotal.value - Number(report.value.disk_free_bytes ?? host.value?.disk_free_bytes ?? diskTotal.value) : 0)
const ratio = (used: number, total: number) => total ? Math.min(1, Math.max(0, used / total)) : 0
const meters = computed(() => [
  { key: 'mem', label: '内存', used: memUsed.value, total: memTotal.value, high: policy.value.mem_high ?? 0.85 },
  { key: 'disk', label: '磁盘', used: diskUsed.value, total: diskTotal.value, high: policy.value.disk_high ?? 0.8 },
])
const states = computed(() => (Object.keys(STATE) as (keyof typeof STATE)[]).map(s => ({ state: s, count: props.overview.environments[s] ?? 0 })).filter(s => s.count > 0))
const alerts = computed(() => {
  const out: string[] = []
  if (!host.value) return out
  if (host.value.status === 'offline') out.push(`运行服务器离线：最近一次连接 ${host.value.last_seen_at ? formatTime(host.value.last_seen_at) : '从未'}。环境不会被删除，回到在线后继续。`)
  const disk = ratio(diskUsed.value, diskTotal.value)
  if (disk > (policy.value.disk_emergency ?? 0.95)) out.push(`磁盘告急（已用 ${Math.round(disk * 100)}%）：暂停新建环境，一次性运行已停止。`)
  else if (disk > (policy.value.disk_high ?? 0.8)) out.push(`磁盘超过高水位（已用 ${Math.round(disk * 100)}%）：正在按保留分归档停止的环境、清理没人用的镜像。`)
  if (ratio(memUsed.value, memTotal.value) > (policy.value.mem_high ?? 0.85)) out.push('内存超过高水位：正在按保留分停止空闲的环境。')
  if (Number(report.value.unlabeled_containers ?? 0) > 0) out.push(`服务器上有 ${report.value.unlabeled_containers} 个不是工作台建的容器：运行服务器最好专用，工作台不会碰它们。`)
  if (!host.value.runtimes.includes('runsc')) out.push('没装 gVisor（runsc）：智能体只能选 runc。重新部署运行服务器会装上。')
  return out
})
const limit = (item: Schema['RunnerInstanceUsage'], key: string) => item.limits[key] ?? 0
</script>

<template>
  <section v-if="!host" class="panel empty-state" aria-label="还没有运行服务器">
    <p>还没有接入运行服务器。智能体要在容器里执行命令，先在“设置”里添加一台（专用的 Linux 服务器，装 Docker 与 gVisor）。</p>
    <button type="button" class="primary-button" @click="$emit('settings')">去添加运行服务器</button>
  </section>
  <template v-else>
    <section class="panel runner-card" aria-label="运行服务器">
      <div class="section-heading">
        <h2>{{ host.name }} <span class="badge" :data-status="host.status">{{ HOST_STATUS[host.status] }}</span></h2>
        <p class="form-help">最近连接 {{ host.last_seen_at ? formatTime(host.last_seen_at) : '从未' }}</p>
      </div>
      <dl class="agent-facts">
        <div><dt>runner-agent</dt><dd>{{ host.agent_version ?? '—' }}</dd></div>
        <div><dt>Docker</dt><dd>{{ host.docker_version ?? '—' }}</dd></div>
        <div><dt>运行时</dt><dd>{{ host.runtimes.length ? host.runtimes.join('、') : '—' }}（gVisor {{ host.runtimes.includes('runsc') ? '已装' : '没装' }}）</dd></div>
        <div><dt>/dev/kvm</dt><dd>{{ host.kvm ? '有（可以跑安卓模拟器等）' : '没有' }}</dd></div>
        <div><dt>CPU</dt><dd>{{ host.cpus ?? '—' }} 核<template v-if="report.load !== undefined && report.load !== null">，负载 {{ Number(report.load).toFixed(2) }}</template></dd></div>
      </dl>
      <ul class="host-meters runner-meters" aria-label="资源用量">
        <li v-for="m in meters" :key="m.key" :class="{ 'is-danger': ratio(m.used, m.total) > m.high }">
          <span>{{ m.label }} <b>{{ bytes(m.used) }}</b> / {{ bytes(m.total || null) }}<template v-if="ratio(m.used, m.total) > m.high">（超过高水位）</template></span>
          <span class="host-bar" role="meter" :aria-label="`${m.label}用量`" :aria-valuenow="Math.round(ratio(m.used, m.total) * 100)" aria-valuemin="0" aria-valuemax="100"><span :style="{ width: `${Math.round(ratio(m.used, m.total) * 100)}%` }" /></span>
        </li>
        <li><span>磁盘里：镜像 <b>{{ bytes(report.images_bytes) }}</b> · 容器 <b>{{ bytes(report.containers_bytes) }}</b> · 数据卷 <b>{{ bytes(report.volumes_bytes) }}</b></span></li>
      </ul>
      <ul v-if="alerts.length" class="host-alerts" aria-label="告警"><li v-for="a in alerts" :key="a">{{ a }}</li></ul>
    </section>

    <section class="panel runner-card" aria-label="环境数">
      <h2>环境</h2>
      <p v-if="!states.length" class="empty-state">还没有环境。打开实例卡片上的“运行环境权限”后，智能体可以自己建。</p>
      <ul v-else class="runner-chips">
        <li v-for="s in states" :key="s.state"><button type="button" class="secondary-button" @click="$emit('environments', s.state)">{{ STATE[s.state] }} {{ s.count }}</button></li>
      </ul>
    </section>

    <section class="panel runner-card" aria-label="智能体的用量">
      <h2>智能体的用量与限额</h2>
      <p v-if="!overview.instances.length" class="empty-state">还没有实例打开运行环境权限（在 OpenClaw 实例的卡片上打开）。</p>
      <div v-else class="table-scroll">
        <table class="agent-table">
          <thead><tr><th>实例</th><th>权限</th><th>运行中 / 上限</th><th>常驻</th><th>内存（MB）</th><th>CPU（核）</th></tr></thead>
          <tbody>
            <tr v-for="item in overview.instances" :key="item.instance_id">
              <td>{{ item.name }}</td>
              <td>{{ item.enabled ? '已打开' : '已关闭' }}</td>
              <td>{{ item.running }} / {{ limit(item, 'envs') }}</td>
              <td>{{ item.persistent }} / {{ limit(item, 'persistent') }}</td>
              <td>{{ item.memory_mb }} / {{ limit(item, 'memory_mb') }}</td>
              <td>{{ item.cpus }} / {{ limit(item, 'cpus') }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="panel runner-card" aria-label="最近的回收">
      <h2>最近的回收</h2>
      <p v-if="!overview.recent.length" class="empty-state">还没有回收记录。</p>
      <ul v-else class="im-contact-list">
        <li v-for="item in overview.recent" :key="`${item.environment_id}:${item.at}`">
          <span>{{ formatTime(String(item.at)) }} · <span class="badge">{{ STATE[item.state as keyof typeof STATE] ?? item.state }}</span> · <span class="preserve-text">{{ item.purpose }}</span> · {{ item.reason }}</span>
        </li>
      </ul>
      <p class="form-help">回收按方案的规则自动进行：空闲到点先停止、再归档（常驻先做加密快照）；删除条件触发后有宽限期；资源紧张时按保留分（重建耗时 × 使用频率 ÷ 占用，空闲越久越低）先回收分低的。类别的说明：{{ Object.values(LIFETIME).join('、') }}。</p>
    </section>
  </template>
</template>
