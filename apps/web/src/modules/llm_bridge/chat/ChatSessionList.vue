<script setup lang="ts">
import { RouterLink } from 'vue-router'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { Archive, Columns3, Plus, RefreshCw, Search } from '@lucide/vue'
import DataState from '../../../components/DataState.vue'
import { MODES, platformName, seconds, type Bridge, type Platform } from '../api'
import { PLATFORM_MARK as MARK, groupByDay, listTime, ownerText, type ChatGroup, type ChatSession, type PlatformChoice, type SessionFilter } from './chatApi'

// 左栏：选 MCP 管理器、新建、按标题搜索、按平台筛选，会话按“今天 / 昨天 / 更早”分组。
// 最上面是“同问”：多模型同问的分组，可以跨管理器，所以不随选中的管理器和平台筛选变（按标题搜索、看已归档的照常）。
// 只显示和发出事件，不发请求；数据由 LlmChatWorkspace 给。
const props = defineProps<{
  bridges: Bridge[]; managerId: string | null; choices: PlatformChoice[]
  sessions: ChatSession[]; loading: boolean; error: unknown
  groups: ChatGroup[]; selectedGroup: string | null; canCompare: boolean
  filter: SessionFilter; selected: string | null; canCreate: boolean
  syncing: boolean; syncNotice: string; syncError: string
  // 后面还有没显示的会话：load 可以再读一批；records 是已经读到上限，更早的到“会话记录”里找
  more?: 'load' | 'records' | null
  // 选中的管理器连得上吗：checking 还在读状态，off 是已停用
  connection?: 'checking' | 'ok' | 'down' | 'off'
}>()
const emit = defineEmits<{ 'update:managerId': [id: string]; 'update:filter': [filter: SessionFilter]; select: [id: string]; create: []; sync: []; retry: []; compare: []; 'open-group': [id: string]; more: [] }>()

const days = computed(() => groupByDay(props.sessions))
const asked = computed(() => { const q = props.filter.q.toLowerCase(); return q ? props.groups.filter(g => g.title.toLowerCase().includes(q)) : props.groups })
const set = (patch: Partial<SessionFilter>) => emit('update:filter', { ...props.filter, ...patch })
const CONNECTION = { checking: '正在检查', ok: '已连接', down: '连不上', off: '已停用' } as const

// 搜索框：停止输入一小会儿再查，不是每个字都发请求
const q = ref(props.filter.q)
let timer: ReturnType<typeof setTimeout> | undefined
watch(q, value => { clearTimeout(timer); timer = setTimeout(() => { if (value.trim() !== props.filter.q) set({ q: value.trim() }) }, 250) })
watch(() => props.filter.q, value => { if (value !== q.value.trim()) q.value = value })
onBeforeUnmount(() => clearTimeout(timer))

const emptyText = computed(() => {
  if (props.filter.q) return '没有标题里带这几个字的会话。'
  if (props.filter.status === 'archived') return '没有已归档的会话。'
  return props.filter.platform ? `「${platformName(props.filter.platform)}」上还没有会话。` : '这个 MCP 管理器上还没有会话：点“新建会话”开始。'
})
</script>

<template>
  <aside class="lc-side" aria-label="会话列表">
    <div class="lc-side-top">
      <div class="lc-manager-head">
        <label class="lc-label" for="lc-manager">MCP 管理器</label>
        <span v-if="connection" class="lc-conn" :data-state="connection">{{ CONNECTION[connection] }}</span>
      </div>
      <select id="lc-manager" :value="managerId ?? ''" @change="emit('update:managerId', ($event.target as HTMLSelectElement).value)">
        <option v-for="b in bridges" :key="b.id" :value="b.id">{{ b.name }}{{ b.enabled ? '' : '（已停用）' }}</option>
      </select>
      <button type="button" class="primary-button lc-new" :disabled="!canCreate" @click="emit('create')"><Plus aria-hidden="true" />新建会话</button>
      <button type="button" class="asv-link lc-compare-link" :disabled="!canCompare" @click="emit('compare')"><Columns3 aria-hidden="true" />多模型同问</button>
    </div>
    <div class="lc-search">
      <Search aria-hidden="true" />
      <input v-model="q" type="search" maxlength="120" aria-label="按标题搜索会话" placeholder="搜索会话标题" />
    </div>
    <div class="lc-filter" role="group" aria-label="按平台筛选">
      <button type="button" :aria-pressed="!filter.platform" @click="set({ platform: null })">全部</button>
      <button v-for="c in choices" :key="c.id" type="button" :aria-pressed="filter.platform === c.id" :title="c.loggedIn ? undefined : '这个平台还没有登录'" @click="set({ platform: c.id as Platform })">
        {{ platformName(c.id) }}<span v-if="!c.loggedIn" class="lc-off">未登录</span>
      </button>
    </div>

    <div class="lc-list">
      <section v-if="asked.length" aria-label="同问">
        <h3>同问</h3>
        <ul>
          <li v-for="g in asked" :key="g.id">
            <button type="button" class="lc-item" :aria-current="g.id === selectedGroup ? 'true' : undefined" @click="emit('open-group', g.id)">
              <span class="lc-mark is-group" aria-hidden="true">同</span>
              <span class="lc-item-main">
                <strong>{{ g.title || '（没有标题）' }}</strong>
                <span class="lc-item-meta">同问 {{ g.members.length }} 个模型 · {{ g.owner_kind === 'me' ? '我' : g.owner || '别的管理员' }}</span>
              </span>
            </button>
          </li>
        </ul>
      </section>
      <DataState v-if="loading || error" :loading="loading" :error="error" :empty="false" empty-text="" @retry="emit('retry')" />
      <p v-else-if="!sessions.length" class="lc-none">{{ emptyText }}</p>
      <section v-for="g in days" v-else :key="g.name" :aria-label="g.name">
        <h3>{{ g.name }}</h3>
        <ul>
          <li v-for="s in g.items" :key="s.session_id">
            <button type="button" class="lc-item" :aria-current="s.session_id === selected ? 'true' : undefined" @click="emit('select', s.session_id)">
              <span class="lc-mark" :data-platform="s.platform" aria-hidden="true">{{ MARK[s.platform] ?? '?' }}</span>
              <span class="lc-item-main">
                <span class="lc-item-top"><strong>{{ s.title || '（没有标题）' }}</strong><time :datetime="new Date(s.updated_at * 1000).toISOString()" :title="seconds(s.updated_at)">{{ listTime(s.updated_at) }}</time></span>
                <span class="lc-item-meta">{{ platformName(s.platform) }} · {{ MODES[s.mode] ?? s.mode }} · {{ ownerText(s) }}<span v-if="s.busy" class="lc-busy">回答中</span></span>
              </span>
            </button>
          </li>
        </ul>
      </section>
      <p v-if="more && !loading && !error" class="lc-more">
        <button v-if="more === 'load'" type="button" class="asv-link" @click="emit('more')">加载更多</button>
        <template v-else>只显示最近 200 个：更早的到 <RouterLink class="asv-link" :to="{ path: '/llm_bridge', query: { view: 'records' } }">会话记录</RouterLink> 里找（那里还能在内容里搜索）。</template>
      </p>
    </div>

    <div class="lc-side-foot">
      <div class="lc-foot-buttons">
        <button type="button" class="lc-foot-btn" title="把官方程序里已有的会话同步到列表" :disabled="syncing || !managerId" @click="emit('sync')"><RefreshCw aria-hidden="true" />{{ syncing ? '正在同步…' : '同步官方会话' }}</button>
        <button type="button" class="lc-foot-btn" :aria-pressed="filter.status === 'archived'" :title="filter.status === 'archived' ? '回到进行中的会话' : '看已归档的会话'" @click="set({ status: filter.status === 'archived' ? 'active' : 'archived' })"><Archive aria-hidden="true" />已归档</button>
      </div>
      <p v-if="syncError" class="lc-note is-red" role="alert">{{ syncError }}</p>
      <p v-else-if="syncNotice" class="lc-note" role="status">{{ syncNotice }}</p>
    </div>
  </aside>
</template>
