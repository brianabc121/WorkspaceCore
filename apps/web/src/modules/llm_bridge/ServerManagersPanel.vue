<script setup lang="ts">
import { computed, toRef } from 'vue'
import { RouterLink } from 'vue-router'
import { useAuthStore } from '../../stores/auth'
import { isModuleDisabled } from '../../services/api'
import { formatTime } from '../../features/time'
import { bridgeError, networkName, slotNames, useLlmBridge, type ServerManager, type TargetKind } from './api'

// 服务器页面上的“MCP 管理器”区块（docs/v2/LLM_BRIDGE_DEPLOY_PLAN.md 第 7 节；模块定义的 serverPanels）：
// 智能体服务器的详情和容器服务器“服务器设置”里各显示一个，列出这台服务器上由工作台部署的 MCP 管理器。
// 这里只给入口：打开聊天、去管理页（重新部署、重启、卸载都在那里）、在这台服务器上再部署一个（owner）。
// 这些容器不归两个服务器页面的管理程序管，所以这里没有任何操作按钮。
const props = defineProps<{ kind: TargetKind; serverId: string; serverName: string }>()
const auth = useAuthStore()
const api = useLlmBridge(auth.workspaceId ?? '')
const query = api.useServerManagers(props.kind, toRef(() => props.serverId))
const managers = computed(() => query.data.value?.managers ?? [])
// 部署入口：只有 owner 能部署，而且这个工作台要带着管理器的构建文件
const canDeploy = computed(() => api.canWrite.value && !!query.data.value?.available)
// 外层和所在页面的其他区块一样：智能体服务器详情是 ws-panel + h3，容器服务器的设置是 panel + h2
const agent = computed(() => props.kind === 'agent_server')
function state(m: ServerManager) {
  if (!m.enabled) return { text: '已停用', tone: 'tone-muted' }
  if (m.reachable === null) return { text: '没有检查', tone: 'tone-muted' }
  return m.reachable ? { text: '在线', tone: 'tone-green' } : { text: '离线', tone: 'tone-red' }
}
</script>

<template>
  <!-- 模块在这个工作空间停用了（接口 404 MODULE_DISABLED）：区块不显示，宿主随后也会收起它 -->
  <section v-if="!isModuleDisabled(query.error.value)" :class="agent ? 'ws-panel asv-section' : 'panel runner-card'" aria-label="MCP 管理器">
    <div class="asv-panel-head">
      <component :is="agent ? 'h3' : 'h2'">MCP 管理器<template v-if="query.data.value"> · {{ managers.length }}</template></component>
      <button v-if="query.data.value" type="button" class="asv-link" :disabled="query.isFetching.value" @click="query.refetch()">{{ query.isFetching.value ? '正在检查…' : '重新检查' }}</button>
    </div>
    <p v-if="query.isPending.value" role="status" class="form-help">正在读取这台服务器上的 MCP 管理器…</p>
    <p v-else-if="query.error.value && !query.data.value" role="alert" class="form-help">读不到这台服务器上的 MCP 管理器：{{ bridgeError(query.error.value) }} <button type="button" class="asv-link" @click="query.refetch()">重试</button></p>
    <template v-else>
      <div v-if="managers.length" class="ws-table-wrap">
        <table class="ws-table asv-table lb-server-managers" :aria-label="`${serverName}上的 MCP 管理器`">
          <thead><tr><th scope="col">名称</th><th scope="col">状态</th><th scope="col">网络方式</th><th scope="col">部署时间</th><th scope="col">已登录的账号</th><th scope="col"><span class="sr-only">操作</span></th></tr></thead>
          <tbody>
            <tr v-for="m in managers" :key="m.bridge_id">
              <th scope="row" data-label="名称">{{ m.name }}<span v-if="m.deployment.outdated" class="asv-sub">有新版本，可以到管理页重新部署</span></th>
              <td data-label="状态"><span class="asv-state" :class="state(m).tone">{{ state(m).text }}</span></td>
              <td data-label="网络方式">{{ networkName(m.deployment.mode, m.deployment.port) }}</td>
              <td data-label="部署时间" class="asv-nowrap">{{ formatTime(m.deployment.deployed_at) }}</td>
              <td data-label="已登录的账号">{{ m.logged_in.length ? slotNames(m.logged_in).join('、') : m.reachable ? '还没有登录' : '—' }}</td>
              <td class="lb-server-links">
                <RouterLink class="asv-link" :to="{ path: '/llm_bridge', query: { m: m.bridge_id } }" :aria-label="`打开聊天：${m.name}`">打开聊天</RouterLink>
                <RouterLink class="asv-link" :to="{ path: '/llm_bridge', query: { view: 'manage', m: m.bridge_id } }" :aria-label="`管理：${m.name}`">管理</RouterLink>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="form-help">这台服务器上还没有工作台部署的 MCP 管理器。</p>
      <p class="form-help">这些容器不受本页面的重启、清理、回收和卸载影响；移除这台服务器后它们也会继续运行。重新部署、重启、卸载在“大模型客户端”的管理页面上做。</p>
      <p v-if="canDeploy"><RouterLink class="asv-link" :to="{ path: '/llm_bridge', query: { view: 'manage', deploy: `${kind}:${serverId}` } }">在这台服务器上部署 MCP 管理器</RouterLink></p>
    </template>
  </section>
</template>

<style scoped>
.lb-server-managers tbody tr { cursor: default; }
.lb-server-links { white-space: nowrap; }
.lb-server-links .asv-link + .asv-link { margin-left: 14px; }
.asv-link:disabled { opacity: .5; cursor: default; text-decoration: none; }
section > p { margin: 0; }
@media (max-width: 760px) {
  .lb-server-links { grid-column: 1 / -1; }
}
</style>
