<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useAuthStore } from '../../stores/auth'
import { dataOf } from '../../services/api'
import { formatTime } from '../time'
import { agentError } from './queries'
import { useAgentAction } from './action'
import { ALERT, RANGE, bytes, type HostRange } from './host'
import { SERVER_KIND, agentMemPercent, serverState, useServer } from './servers'
import { sshError } from './ssh'
import HostCards from './HostCards.vue'
import HostTrends from './HostTrends.vue'
import ShareBar from './ShareBar.vue'
import AgentTable from './AgentTable.vue'
import AgentTrend from './AgentTrend.vue'
import ServerSsh from './ServerSsh.vue'
import OpsPanel from './OpsPanel.vue'

// 服务器详情（T-0147，docs/v2/MULTI_OPENCLAW_PLAN.md 第 7.2 节）：整机当前值与趋势、每个智能体占整机的份额、
// 智能体表格（可排序、单个智能体的趋势）、SSH 与专用密钥、服务器级运维。
const props = defineProps<{ serverId: string }>()
const emit = defineEmits<{ back: [] }>()
const auth = useAuthStore(), scope = auth.workspaceId!
const range = ref<HostRange>('24h'), selected = ref<string | null>(null)
const detail = useServer(scope, () => props.serverId, () => range.value)
const data = computed(() => detail.data.value?.server.id === props.serverId ? detail.data.value : undefined)
const server = computed(() => data.value?.server)
const { busy, error, notice, act, header } = useAgentAction(scope, sshError)
const renaming = ref(false), name = ref('')
watch(() => props.serverId, () => { selected.value = null; renaming.value = false })

// 颜色跟着智能体：按名字的固定顺序给 1…8 号色（排序、筛选都不改）
const ordered = computed(() => [...(data.value?.agents ?? [])].sort((a, b) => a.name.localeCompare(b.name, 'zh-CN') || a.instance_id.localeCompare(b.instance_id)))
const slots = computed(() => new Map(ordered.value.map((a, i) => [a.instance_id, i + 1])))
const cpuShares = computed(() => ordered.value.map(a => ({ key: a.instance_id, label: a.name, value: a.load?.available ? a.load.cpu_percent : null })))
const memShares = computed(() => ordered.value.map(a => ({ key: a.instance_id, label: a.name, value: a.load?.available ? agentMemPercent(a, data.value?.current?.mem_total) : null })))
const memTotalPercent = computed(() => {
  const c = data.value?.current
  return c?.mem_total && c.mem_used !== null ? (100 * c.mem_used) / c.mem_total : null
})
const memDetail = (value: number) => bytes(((data.value?.current?.mem_total ?? 0) * value) / 100)
const selectedRow = computed(() => data.value?.agents.find(a => a.instance_id === selected.value) ?? null)

function startRename() { name.value = server.value?.name ?? ''; renaming.value = true }
async function rename() {
  const s = server.value
  const next = name.value.trim()
  if (!s || !next) return
  const ok = await act(async () => dataOf(await auth.api.PATCH('/api/v1/agents/servers/{server_id}', { params: { header, path: { server_id: s.id } }, body: { name: next, version: s.version } })), '名称已保存')
  if (ok) renaming.value = false
}
</script>

<template>
  <div class="server-detail">
    <div class="section-heading">
      <button type="button" class="secondary-button" @click="emit('back')">← 返回服务器列表</button>
    </div>
    <p v-if="detail.isPending.value" role="status">正在加载服务器…</p>
    <p v-else-if="detail.error.value" role="alert">{{ agentError(detail.error.value) }}</p>
    <template v-else-if="data && server">
      <section class="panel" :aria-label="`服务器：${server.name}`">
        <div class="section-heading">
          <h2 v-if="!renaming">{{ server.name }} <span class="badge">{{ SERVER_KIND[server.kind] }}</span> <span class="badge">{{ serverState(server) }}</span></h2>
          <form v-else class="im-group-add" aria-label="改名称" @submit.prevent="rename">
            <label class="form-field"><span>名称</span><input v-model.trim="name" maxlength="100" /></label>
            <div class="form-actions"><button type="submit" class="primary-button" :disabled="busy || !name.trim()">保存</button><button type="button" class="secondary-button" @click="renaming = false">取消</button></div>
          </form>
          <button v-if="!renaming" type="button" class="secondary-button" :disabled="busy" @click="startRename">改名称</button>
        </div>
        <p class="form-help">
          {{ server.instances_total }} 个智能体，{{ server.instances_online }} 个在线<template v-if="server.reported_at"> · 最后上报 {{ formatTime(server.reported_at) }}</template><template v-if="server.ssh"> · {{ server.ssh.username }}@{{ server.ssh.host }}:{{ server.ssh.port }}</template>
        </p>
        <ul v-if="server.alerts.length" class="host-alerts" aria-label="正在提醒的告警">
          <li v-for="a in server.alerts" :key="a"><span class="host-alert-icon" aria-hidden="true">!</span>{{ ALERT[a] }}</li>
        </ul>
        <p v-if="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
      </section>

      <ServerSsh :detail="data" @removed="emit('back')" />

      <section class="panel" aria-label="整机负载">
        <h3>整机负载</h3>
        <p v-if="!data.current" class="form-help">还没有收到这台服务器的上报：上面的实例部署好、每分钟上报一次后显示。</p>
        <template v-else>
          <HostCards :current="data.current" :alerts="server.alerts" />
          <div class="server-shares">
            <ShareBar title="CPU：每个智能体占多少" :total="data.current.cpu_percent" :shares="cpuShares" />
            <ShareBar title="内存：每个智能体占多少" :total="memTotalPercent" :shares="memShares" :detail="memDetail" />
          </div>
          <div class="segmented host-range" role="group" aria-label="趋势的时间范围">
            <button v-for="(label, key) in RANGE" :key="key" type="button" :aria-pressed="range === key" @click="range = key">最近 {{ label }}</button>
          </div>
          <HostTrends :points="data.points" :range="range" :cores="data.current.cores" />
        </template>
      </section>

      <section class="panel" aria-label="这台服务器上的智能体">
        <h3>这台服务器上的智能体（{{ data.agents.length }}）</h3>
        <p v-if="!data.agents.length" class="empty-state">这台服务器上还没有智能体。到“实例”页的实例卡片里选“部署到服务器”。</p>
        <template v-else>
          <AgentTable :rows="data.agents" :slots="slots" :selected="selected" @select="selected = $event" />
          <AgentTrend v-if="selectedRow" :key="selectedRow.instance_id" :instance-id="selectedRow.instance_id" :name="selectedRow.name" :range="range" />
        </template>
      </section>

      <OpsPanel v-if="server.kind === 'ssh'" kind="server" :target-id="server.id" />
    </template>
  </div>
</template>
