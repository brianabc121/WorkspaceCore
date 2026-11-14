<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import DataState from '../../../components/DataState.vue'
import { useMediaQuery } from '../../../composables/useMediaQuery'
import { isModuleDisabled } from '../../../services/api'
import { bridgeError, isBridgeDown, useLlmBridge } from '../api'
import { chatError, platformChoices, useLlmChat, type SessionFilter } from './chatApi'
import ChatSessionList from './ChatSessionList.vue'
import ChatConversation from './ChatConversation.vue'
import ChatNewSession from './ChatNewSession.vue'
import ChatCompare from './ChatCompare.vue'
import ChatCompareNew from './ChatCompareNew.vue'
import '../llm_bridge.css'
import './llm_chat.css'

// 大模型聚合聊天（docs/v2/LLM_CHAT_PLAN.md）：左边会话列表，中间对话，右边会话信息。
// 选中的管理器和会话放在地址的查询参数里（?m=<管理器>&s=<会话>，新建是 ?m=…&new=1）：能收藏、能后退。
// 多模型同问：一组是 ?g=<分组>，新建是 ?compare=1（docs/v2/LLM_CHAT_PLAN.md 第 3.4 节）。
// 窄屏时列表和对话分成两屏。owner 和 admin 都能用（后端同样校验）。
const props = defineProps<{ scope: string }>()
const route = useRoute(), router = useRouter()
const api = useLlmBridge(props.scope), chat = useLlmChat(props.scope)
const bridges = api.useBridges()
const list = computed(() => bridges.data.value ?? [])

const one = (value: unknown) => (typeof value === 'string' && value ? value : null)
const managerId = computed(() => {
  const asked = one(route.query.m)
  return (list.value.find(b => b.id === asked) ?? list.value.find(b => b.enabled) ?? list.value[0])?.id ?? null
})
const manager = computed(() => list.value.find(b => b.id === managerId.value) ?? null)
const sessionId = computed(() => { const s = one(route.query.s); return s && /^bs_[0-9a-f]{20}$/.test(s) ? s : null })
const groupId = computed(() => { const g = one(route.query.g); return g && /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(g) && !sessionId.value ? g : null })
const creating = computed(() => route.query.new === '1' && !sessionId.value && !groupId.value)
const comparing = computed(() => route.query.compare === '1' && !sessionId.value && !groupId.value && !creating.value)

const status = api.useStatus(managerId)
const choices = computed(() => platformChoices(status.data.value))
const statusProblem = computed(() => {
  if (status.error.value) return isBridgeDown(status.error.value) ? bridgeError(status.error.value) : `读不到这个管理器的状态：${bridgeError(status.error.value)}`
  const data = status.data.value
  return data && !data.reachable ? `这个 MCP 管理器连不上${data.error ? `：${data.error}` : '。'}` : ''
})
const filter = ref<SessionFilter>({ q: '', platform: null, status: 'active' })
const sessions = chat.useSessions(managerId, filter)
// 一个管理器都没有时页面只显示说明，不用读分组
const groups = chat.useGroups(computed(() => filter.value.status), computed(() => list.value.length > 0))

// jsdom 里没有 matchMedia，按宽屏处理
const wide = useMediaQuery('(min-width: 901px)')
const narrow = computed(() => !wide.value)
const opened = computed(() => !!sessionId.value || !!groupId.value || creating.value || comparing.value)
const showList = computed(() => wide.value || !opened.value)
const showMain = computed(() => wide.value || opened.value)

const here = (extra: Record<string, string> = {}) => ({ path: route.path, query: { ...(managerId.value ? { m: managerId.value } : {}), ...extra } })
// 新建后带进会话的草稿和说明（第一条消息没发出去时）
const carry = ref<{ session: string; draft: string; error: string } | null>(null)
function pickManager(id: string, extra: Record<string, string> = {}) {
  filter.value = { ...filter.value, platform: null }
  syncNotice.value = syncError.value = ''
  void router.replace({ path: route.path, query: { m: id, ...extra } })
}
function open(id: string) { carry.value = null; void router.push(here({ s: id })) }
function created(id: string, draft: string, error: string) {
  carry.value = draft || error ? { session: id, draft, error } : null
  void router.replace(here({ s: id }))
}
function refreshList() { if (managerId.value) void chat.invalidateSessions(managerId.value) }
function openGroup(id: string, replace = false) { carry.value = null; void router[replace ? 'replace' : 'push'](here({ g: id })) }
// 同问里的会话分散在几个管理器上：都刷新一下
function refreshAll() { for (const b of list.value) void chat.invalidateSessions(b.id) }

const syncing = ref(false), syncNotice = ref(''), syncError = ref('')
async function sync() {
  const id = managerId.value
  if (!id || syncing.value) return
  syncing.value = true; syncNotice.value = syncError.value = ''
  try {
    const out = await chat.syncSessions(id)
    syncNotice.value = out.sync_failed ? `已同步；这些平台的会话读不到：${out.sync_failed}` : '已同步官方程序里的会话。'
  } catch (e) { syncError.value = chatError(e) } finally { syncing.value = false }
}
</script>

<template>
  <div class="lc-page">
    <p v-if="isModuleDisabled(bridges.error.value)" role="status">功能已停用：大模型客户端在当前工作空间没有启用。</p>
    <DataState v-else-if="bridges.isPending.value || bridges.error.value" :loading="bridges.isPending.value" :error="bridges.error.value" :empty="false" empty-text="" @retry="bridges.refetch()" />
    <div v-else-if="!list.length" class="lb-empty lc-welcome">
      <p><strong>还没有 MCP 管理器。</strong></p>
      <p>先添加一个 MCP 管理器并登录平台账号，然后就能在这里和 ChatGPT / Codex / Claude / WorkBuddy 对话，也能看到智能体开的会话。</p>
      <p><RouterLink class="primary-button" :to="{ path: '/llm_bridge', query: { view: 'manage' } }">去添加 MCP 管理器</RouterLink></p>
    </div>
    <div v-else class="lc-app" :class="{ 'is-narrow': narrow }">
      <ChatSessionList
        v-if="showList" :bridges="list" :manager-id="managerId" :choices="choices" :sessions="sessions.data.value?.sessions ?? []"
        :loading="sessions.isPending.value" :error="sessions.data.value ? null : sessions.error.value" :filter="filter" :selected="sessionId"
        :can-create="!!manager?.enabled" :syncing="syncing" :sync-notice="syncNotice" :sync-error="syncError"
        :groups="groups.data.value ?? []" :selected-group="groupId" :can-compare="list.some(b => b.enabled)"
        @update:manager-id="pickManager($event)" @update:filter="filter = $event" @select="open" @create="router.push(here({ new: '1' }))" @sync="sync" @retry="sessions.refetch()"
        @compare="router.push(here({ compare: '1' }))" @open-group="openGroup($event)"
      />
      <section v-if="showMain" class="lc-main" aria-label="对话">
        <ChatCompare v-if="groupId" :key="`g:${groupId}`" :chat="chat" :group-id="groupId" :narrow="narrow" @back="router.push(here())" @changed="refreshAll" />
        <ChatCompareNew
          v-else-if="comparing && manager" :chat="chat" :api="api" :bridges="list" :manager-id="manager.id" :narrow="narrow"
          @created="openGroup($event, true); refreshAll()" @cancel="router.push(here())"
        />
        <ChatNewSession
          v-else-if="creating && manager" :key="`new:${manager.id}`" :chat="chat" :bridges="list" :manager="manager" :choices="choices"
          :status-loading="status.isPending.value" :status-problem="statusProblem" :narrow="narrow"
          @update:manager-id="pickManager($event, { new: '1' })" @created="created" @cancel="router.push(here())"
        />
        <ChatConversation
          v-else-if="sessionId && manager" :key="`${manager.id}:${sessionId}`" :chat="chat" :bridge="manager" :session-id="sessionId" :narrow="narrow"
          :initial-draft="carry?.session === sessionId ? carry.draft : ''" :initial-error="carry?.session === sessionId ? carry.error : ''"
          @back="router.push(here())" @changed="refreshList"
        />
        <div v-else class="lc-blank">
          <p><strong>{{ manager ? `MCP 管理器「${manager.name}」` : '选一个 MCP 管理器' }}</strong></p>
          <p v-if="statusProblem" role="alert">{{ statusProblem }}</p>
          <template v-else-if="status.data.value">
            <p v-if="!choices.some(c => c.loggedIn)">这个管理器上还没有登录任何平台。<RouterLink class="asv-link" :to="{ path: '/llm_bridge', query: { view: 'manage' } }">去登录</RouterLink></p>
            <p v-else>在左边选一个会话接着聊，或者点“新建会话”；想同时问几个模型就点“多模型同问”。智能体经这个管理器开的会话也在列表里。</p>
          </template>
          <p v-if="manager && !manager.enabled">这个管理器已停用：可以查看会话，不能新建和发送。</p>
        </div>
      </section>
    </div>
  </div>
</template>
