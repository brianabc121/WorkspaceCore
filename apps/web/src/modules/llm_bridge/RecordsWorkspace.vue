<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { RefreshCw, Search } from '@lucide/vue'
import ManagementPage from '../../components/management/ManagementPage.vue'
import ManagementSection from '../../components/management/ManagementSection.vue'
import ActionFeedback from '../../components/ActionFeedback.vue'
import { formatTime } from '../../features/time'
import { MODES, OWNER_KIND, PLATFORMS, bridgeError, platformName, seconds, sourceTasks, useLlmBridge, type IndexFilter, type IndexSession, type Platform, type SearchHit, type UsageRow } from './api'

// 会话记录（docs/v2/LLM_BRIDGE_IMPROVE_PLAN.md 4.2）：管理员事后查找、回溯。
// - 列表来自工作台的会话索引：跨 MCP 管理器，按发起人、平台、用法、时间、有没有失败筛选；不含对话内容。
// - 全文搜索：在各个管理器的提问和回答里找，内容不经过工作台的数据库。
// - 用量：按天、管理器、平台、发起人汇总。token 是官方程序报的数，不是账单。
// 点一行进聊天页看那个会话的全部内容。
const props = defineProps<{ scope: string }>()
const api = useLlmBridge(props.scope)
const bridges = api.useBridges()
const owners = api.useIndexOwners()
const LIMIT = 50

// ---- 筛选 ----
const bridgeId = ref(''), who = ref(''), platform = ref<Platform | ''>(''), mode = ref<'' | 'chat' | 'dev'>(''), status = ref<'' | 'active' | 'archived'>('')
const problem = ref(false), since = ref(''), until = ref(''), title = ref('')
const offset = ref(0)
const day = (value: string, end = false) => {
  if (!value) return undefined
  const at = new Date(`${value}T00:00:00`)
  if (end) at.setDate(at.getDate() + 1)
  return at.toISOString()
}
const filter = computed<IndexFilter>(() => {
  const out: IndexFilter = {}
  if (bridgeId.value) out.bridge_id = bridgeId.value
  // 发起人：一类（智能体 / 人 / 官方程序里已有），或者具体某一个
  if (who.value.startsWith('kind:')) out.owner_kind = who.value.slice(5) as IndexFilter['owner_kind']
  else if (who.value) out.owner_id = who.value
  if (platform.value) out.platform = platform.value
  if (mode.value) out.mode = mode.value
  if (status.value) out.status = status.value
  if (problem.value) out.problem = true
  const from = day(since.value), to = day(until.value, true)
  if (from) out.since = from
  if (to) out.until = to
  if (title.value.trim()) out.q = title.value.trim()
  return out
})
watch(filter, () => { offset.value = 0 })
const index = api.useIndex(filter, offset, LIMIT)
const list = computed(() => index.data.value?.sessions ?? [])
const filtered = computed(() => Object.keys(filter.value).length > 0)
function clear() { bridgeId.value = who.value = platform.value = mode.value = status.value = since.value = until.value = title.value = ''; problem.value = false }

const error = ref(''), notice = ref(''), syncing = ref(false)
async function sync() {
  if (syncing.value) return
  syncing.value = true; error.value = notice.value = ''
  try {
    const failed = await api.syncIndex()
    await owners.refetch()
    if (failed.length) error.value = `这些 MCP 管理器没有同步上：${failed.join('、')}（列表里是它们上一次同步到的）`
    else notice.value = '已同步。'
  } catch (e) { error.value = bridgeError(e) } finally { syncing.value = false }
}
const link = (s: { bridge_id: string; session_id: string }) => ({ path: '/llm_bridge', query: { m: s.bridge_id, s: s.session_id } })
const trouble = (s: IndexSession) => [s.turns_failed && `${s.turns_failed} 个失败`, s.turns_interrupted && `${s.turns_interrupted} 个中断`].filter(Boolean).join('、')

// ---- 全文搜索 ----
const query = ref(''), searching = ref(false), searchError = ref('')
const hits = ref<SearchHit[] | null>(null), searched = ref(''), missed = ref<string[]>([])
async function search() {
  const q = query.value.trim()
  if (searching.value) return
  if (q.length < 2) { searchError.value = '至少写两个字。'; return }
  searching.value = true; searchError.value = ''
  try {
    const out = await api.searchSessions(q)
    hits.value = out.hits; missed.value = out.failed; searched.value = q
  } catch (e) { searchError.value = bridgeError(e) } finally { searching.value = false }
}
function clearSearch() { hits.value = null; query.value = searched.value = ''; missed.value = []; searchError.value = '' }

// ---- 用量 ----
const days = ref(30)
const usage = api.useUsage(days)
const rows = computed(() => usage.data.value?.rows ?? [])
// 按发起人汇总一行，下面可以展开看每一天
const byOwner = computed(() => {
  const groups = new Map<string, { key: string; owner: string; kind: string; turns: number; failed: number; interrupted: number; tokens: number; rows: UsageRow[] }>()
  for (const r of rows.value) {
    const key = `${r.owner_kind}:${r.owner_id ?? ''}`
    const g = groups.get(key) ?? { key, owner: r.owner || OWNER_KIND[r.owner_kind] || '—', kind: r.owner_kind, turns: 0, failed: 0, interrupted: 0, tokens: 0, rows: [] }
    g.turns += r.turns; g.failed += r.failed; g.interrupted += r.interrupted; g.tokens += r.tokens; g.rows.push(r)
    groups.set(key, g)
  }
  return [...groups.values()].sort((a, b) => b.tokens - a.tokens || b.turns - a.turns)
})
const total = computed(() => byOwner.value.reduce((sum, g) => ({ turns: sum.turns + g.turns, tokens: sum.tokens + g.tokens }), { turns: 0, tokens: 0 }))
</script>

<template>
  <ManagementPage title="会话记录" description="所有 MCP 管理器上的会话：智能体开的、人在聊天页开的、官方程序里已有的。这里只有“有哪些会话”，对话内容仍然只在各个管理器里；点一行进聊天页看全部内容。">
    <ManagementSection title="查找会话" description="列表每分钟自动同步一次。">
      <template #actions>
        <button type="button" class="secondary-button" :disabled="syncing" @click="sync"><RefreshCw aria-hidden="true" />{{ syncing ? '正在同步…' : '现在同步' }}</button>
      </template>
      <ActionFeedback :error="error" :notice="notice" />
      <form class="lb-filters" aria-label="筛选会话" @submit.prevent>
        <div class="form-field">
          <label for="rec-who">谁建的</label>
          <select id="rec-who" v-model="who">
            <option value="">全部</option>
            <option value="kind:agent">所有智能体</option><option value="kind:user">所有人</option><option value="kind:external">官方程序里已有</option>
            <optgroup v-if="owners.data.value?.some(o => o.owner_id)" label="具体是谁">
              <option v-for="o in (owners.data.value ?? []).filter(x => x.owner_id)" :key="o.owner_id!" :value="o.owner_id!">{{ o.owner || o.owner_id }}（{{ OWNER_KIND[o.owner_kind] }}，{{ o.sessions }} 个）</option>
            </optgroup>
          </select>
        </div>
        <div v-if="(bridges.data.value?.length ?? 0) > 1" class="form-field">
          <label for="rec-bridge">MCP 管理器</label>
          <select id="rec-bridge" v-model="bridgeId"><option value="">全部</option><option v-for="b in bridges.data.value" :key="b.id" :value="b.id">{{ b.name }}</option></select>
        </div>
        <div class="form-field">
          <label for="rec-platform">平台</label>
          <select id="rec-platform" v-model="platform"><option value="">全部</option><option v-for="p in PLATFORMS" :key="p.id" :value="p.id">{{ p.name }}</option></select>
        </div>
        <div class="form-field">
          <label for="rec-mode">用法</label>
          <select id="rec-mode" v-model="mode"><option value="">全部</option><option value="chat">对话</option><option value="dev">开发</option></select>
        </div>
        <div class="form-field">
          <label for="rec-status">状态</label>
          <select id="rec-status" v-model="status"><option value="">全部</option><option value="active">进行中</option><option value="archived">已归档</option></select>
        </div>
        <div class="form-field"><label for="rec-since">最后活动从</label><input id="rec-since" v-model="since" type="date" /></div>
        <div class="form-field"><label for="rec-until">到</label><input id="rec-until" v-model="until" type="date" /></div>
        <div class="form-field"><label for="rec-title">标题里有</label><input id="rec-title" v-model="title" maxlength="120" placeholder="标题的一部分" /></div>
        <label class="check-field"><input v-model="problem" type="checkbox" />只看有失败或中断的</label>
        <button v-if="filtered" type="button" class="asv-link" @click="clear">清除筛选</button>
      </form>
      <p v-if="index.isPending.value" role="status" class="data-state">正在读取会话记录…</p>
      <div v-else-if="index.error.value" class="data-state">
        <p role="alert">{{ bridgeError(index.error.value) }}</p>
        <button type="button" class="secondary-button" @click="index.refetch()">重试</button>
      </div>
      <p v-else-if="!list.length" class="ws-state-line">{{ offset ? '后面没有更多了。' : filtered ? '没有符合条件的会话。' : '还没有会话记录。添加 MCP 管理器并有了会话之后，一分钟内会出现在这里；也可以点“现在同步”。' }}</p>
      <table v-else class="asv-table lb-table" aria-label="会话记录">
        <thead><tr><th scope="col">标题</th><th scope="col">谁建的</th><th scope="col">平台 · 用法</th><th scope="col">MCP 管理器</th><th scope="col">回合</th><th scope="col">token</th><th scope="col">最后活动</th></tr></thead>
        <tbody>
          <tr v-for="s in list" :key="`${s.bridge_id}:${s.session_id}`">
            <th scope="row">
              <RouterLink class="asv-link lb-wrap" :to="link(s)" :aria-label="`打开会话：${s.title || s.session_id}`">{{ s.title || '（没有标题）' }}</RouterLink>
              <span v-if="s.busy" class="asv-state tone-green">正在回答</span>
              <span v-if="s.status === 'archived'" class="asv-state tone-muted">已归档</span>
              <span v-for="t in sourceTasks(s.source)" :key="t.task_id" class="asv-sub">做任务时发起：<RouterLink class="asv-link" :to="`/tasks?task=${t.task_id}`">{{ t.title || t.task_id }}</RouterLink></span>
            </th>
            <td data-label="谁建的">{{ s.owner || '—' }}<span class="asv-sub">{{ OWNER_KIND[s.owner_kind] ?? s.owner_kind }}</span></td>
            <td data-label="平台 · 用法">{{ platformName(s.platform) }} · {{ MODES[s.mode] ?? s.mode }}<span class="asv-sub">{{ s.model || '平台默认模型' }}</span></td>
            <td data-label="MCP 管理器">{{ s.bridge_name }}</td>
            <td data-label="回合">{{ s.turns_total }}<span v-if="trouble(s)" class="asv-sub lb-trouble">{{ trouble(s) }}</span></td>
            <td data-label="token">{{ s.tokens.toLocaleString() }}</td>
            <td data-label="最后活动" class="asv-nowrap">{{ formatTime(s.updated_at) }}</td>
          </tr>
        </tbody>
      </table>
      <div v-if="offset || index.data.value?.next_offset != null" class="lb-pager">
        <button type="button" class="secondary-button" :disabled="!offset" @click="offset = Math.max(0, offset - LIMIT)">上一页</button>
        <button type="button" class="secondary-button" :disabled="index.data.value?.next_offset == null" @click="offset += LIMIT">下一页</button>
      </div>
    </ManagementSection>

    <ManagementSection title="在内容里搜索" description="在所有 MCP 管理器的提问和回答里找一段文字。每个会话给最新的一处。">
      <form class="lb-search" role="search" aria-label="搜索对话内容" @submit.prevent="search">
        <input v-model="query" type="search" maxlength="120" aria-label="要找的文字" placeholder="提问或回答里的一段文字" />
        <button type="submit" class="primary-button" :disabled="searching"><Search aria-hidden="true" />{{ searching ? '正在搜索…' : '搜索' }}</button>
        <button v-if="hits" type="button" class="asv-link" @click="clearSearch">清除</button>
      </form>
      <p v-if="searchError" class="lb-warn is-red" role="alert">{{ searchError }}</p>
      <p v-if="missed.length" class="lb-warn" role="note">这些 MCP 管理器没有搜到（连不上或版本太旧）：{{ missed.join('、') }}</p>
      <template v-if="hits">
        <p v-if="!hits.length" class="ws-state-line">没有找到“{{ searched }}”。</p>
        <ul v-else class="lb-hits" :aria-label="`“${searched}”的搜索结果`">
          <li v-for="h in hits" :key="`${h.bridge_id}:${h.session_id}`">
            <RouterLink class="asv-link" :to="link(h)">{{ h.session?.title || '（没有标题）' }}</RouterLink>
            <span class="asv-sub">{{ h.session ? `${h.session.owner || OWNER_KIND[h.session.owner_kind]} · ${platformName(h.session.platform)} · ` : '' }}{{ h.bridge_name }} · {{ h.role === 'user' ? '在提问里' : '在回答里' }} · {{ seconds(h.at) }}</span>
            <p class="lb-snippet">{{ h.snippet }}</p>
          </li>
        </ul>
      </template>
    </ManagementSection>

    <ManagementSection title="用量" description="回合数和 token。token 是官方程序报的数相加，不是账单；没报的回合（多数失败的回合、CodeBuddy 的积分）算不进来。日期按 UTC。">
      <template #actions>
        <label class="lb-inline">最近 <select v-model.number="days" aria-label="看最近多少天"><option :value="7">7 天</option><option :value="30">30 天</option><option :value="90">90 天</option></select></label>
      </template>
      <p v-if="usage.isPending.value" role="status" class="data-state">正在读取用量…</p>
      <div v-else-if="usage.error.value" class="data-state">
        <p role="alert">{{ bridgeError(usage.error.value) }}</p>
        <button type="button" class="secondary-button" @click="usage.refetch()">重试</button>
      </div>
      <template v-else>
        <p v-if="usage.data.value?.failed.length" class="lb-warn" role="note">这些 MCP 管理器的用量没有取到：{{ usage.data.value.failed.join('、') }}</p>
        <p v-if="!byOwner.length" class="ws-state-line">这段时间没有回合。</p>
        <template v-else>
          <p class="form-help">一共 {{ total.turns.toLocaleString() }} 个回合，{{ total.tokens.toLocaleString() }} 个 token。</p>
          <table class="asv-table lb-table" aria-label="用量">
            <thead><tr><th scope="col">谁</th><th scope="col">回合</th><th scope="col">失败</th><th scope="col">中断</th><th scope="col">token</th></tr></thead>
            <tbody>
              <tr v-for="g in byOwner" :key="g.key">
                <th scope="row">
                  <details>
                    <summary>{{ g.owner }}<span class="asv-sub">{{ OWNER_KIND[g.kind] ?? g.kind }}</span></summary>
                    <ul class="lb-days" :aria-label="`${g.owner}每天的用量`">
                      <li v-for="(r, i) in g.rows" :key="i">{{ r.day }} · {{ platformName(r.platform) }} · {{ r.bridge_name }}：{{ r.turns }} 个回合，{{ r.tokens.toLocaleString() }} 个 token{{ r.failed ? `，${r.failed} 个失败` : '' }}</li>
                    </ul>
                  </details>
                </th>
                <td data-label="回合">{{ g.turns.toLocaleString() }}</td>
                <td data-label="失败">{{ g.failed || '—' }}</td>
                <td data-label="中断">{{ g.interrupted || '—' }}</td>
                <td data-label="token">{{ g.tokens.toLocaleString() }}</td>
              </tr>
            </tbody>
          </table>
        </template>
      </template>
    </ManagementSection>
  </ManagementPage>
</template>
