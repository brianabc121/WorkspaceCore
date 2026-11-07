<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../../stores/auth'
import { agentError, STATUS } from './queries'
import { ALERT, DISK_USED_MAX, MEMORY_USED_MAX, percent } from './host'
import { SERVER_KIND, serverState, useServers, type Server } from './servers'
import ServerAdd from './ServerAdd.vue'
import ServerDetail from './ServerDetail.vue'

// “服务器”标签页（T-0147，docs/v2/MULTI_OPENCLAW_PLAN.md 第 7 节）：每台服务器一张卡片（整机 CPU / 内存 / 磁盘、
// 上面几个智能体、几个在线），点进去看详情；还没归属服务器的实例列在下面。打开哪台服务器记在地址栏（?server=）。
const auth = useAuthStore(), scope = auth.workspaceId!
const route = useRoute(), router = useRouter()
const servers = useServers(scope)
const adding = ref(false)
const items = computed(() => servers.data.value?.items ?? [])
const unassigned = computed(() => servers.data.value?.unassigned ?? [])
const selected = computed(() => typeof route.query.server === 'string' ? route.query.server : null)

function open(id: string | null) {
  adding.value = false
  void router.push({ path: '/agents', query: { tab: 'servers', ...(id ? { server: id } : {}) } })
}
const meters = (s: Server) => s.reported_at ? [
  { key: 'cpu', label: 'CPU', value: s.cpu_percent, danger: s.alerts.includes('load_high') },
  { key: 'mem', label: '内存', value: s.mem_used_percent, danger: s.alerts.includes('memory_low') || (s.mem_used_percent ?? 0) >= MEMORY_USED_MAX },
  { key: 'disk', label: '磁盘', value: s.disk_used_percent, danger: s.alerts.includes('disk_low') || (s.disk_used_percent ?? 0) >= DISK_USED_MAX },
] : []
const bar = (value: number | null | undefined) => ({ width: `${Math.min(100, Math.max(0, value ?? 0))}%` })
</script>

<template>
  <ServerDetail v-if="selected" :server-id="selected" @back="open(null)" />
  <template v-else>
    <div class="section-heading">
      <p class="form-help">一台服务器上可以部署多个 OpenClaw 实例：每个实例一个目录、一个 Compose 项目、一个自己的网络，互不影响。手动部署的实例按上报自动归到服务器。</p>
      <button type="button" class="primary-button" :disabled="adding" @click="adding = true">添加服务器</button>
    </div>
    <ServerAdd v-if="adding" @close="adding = false" @added="open($event)" />
    <p v-if="servers.isPending.value" role="status">正在加载服务器…</p>
    <p v-else-if="servers.error.value" role="alert">{{ agentError(servers.error.value) }}</p>
    <p v-else-if="!items.length && !adding" class="empty-state">还没有服务器。点“添加服务器”经 SSH 添加一台，或者手动部署的实例上报后会自动出现在这里。</p>
    <div class="server-list">
      <article v-for="s in items" :key="s.id" class="panel server-card" :aria-label="`服务器：${s.name}`">
        <h2>{{ s.name }} <span class="badge">{{ SERVER_KIND[s.kind] }}</span> <span class="badge">{{ serverState(s) }}</span></h2>
        <p class="form-help">{{ s.instances_total }} 个智能体，{{ s.instances_online }} 个在线<template v-if="s.cores"> · {{ s.cores }} 核</template><template v-if="s.ssh"> · {{ s.ssh.host }}</template></p>
        <ul v-if="meters(s).length" class="host-meters" aria-label="当前使用率">
          <li v-for="m in meters(s)" :key="m.key" :class="{ 'is-danger': m.danger }">
            <span>{{ m.label }} <b>{{ percent(m.value) }}</b></span>
            <span class="host-bar" aria-hidden="true"><span :style="bar(m.value)" /></span>
          </li>
        </ul>
        <ul v-if="s.alerts.length" class="host-alerts" aria-label="正在提醒的告警">
          <li v-for="a in s.alerts" :key="a"><span class="host-alert-icon" aria-hidden="true">!</span>{{ ALERT[a] }}</li>
        </ul>
        <div class="form-actions"><button type="button" class="secondary-button" @click="open(s.id)">查看详情</button></div>
      </article>
    </div>
    <section v-if="unassigned.length" class="panel agent-section" aria-label="还没归属服务器的实例">
      <h3>还没归属服务器的实例（{{ unassigned.length }}）</h3>
      <p class="form-help">它们还没上报过（或者接入包较旧）。在“实例”页的实例卡片里部署到一台服务器，或者手动指定所在服务器。</p>
      <ul class="im-contact-list"><li v-for="u in unassigned" :key="u.id"><span>{{ u.name }} · {{ STATUS[u.status] }}</span></li></ul>
    </section>
  </template>
</template>
