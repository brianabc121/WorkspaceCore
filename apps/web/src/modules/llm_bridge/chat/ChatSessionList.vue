<script setup lang="ts">
import { RouterLink } from 'vue-router'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { Archive, Columns3, MoreHorizontal, Plus, RefreshCw, Search, Undo2 } from '@lucide/vue'
import DataState from '../../../components/DataState.vue'
import { MODES, platformName, seconds, type Bridge, type Platform } from '../api'
import { groupByDay, listTime, ownerText, sessionState, type ChatGroup, type ChatSession, type PlatformChoice, type SessionFilter } from './chatApi'
import ChatPlatformIcon from './ChatPlatformIcon.vue'

// 会话一栏（效果图第二栏）：标题和数量、“新建”，其余操作（多模型同问、同步官方会话、已归档）在“更多”菜单里；
// 按标题搜索、按平台筛选，会话按“今天 / 昨天 / 更早”分组，每个会话一行标题、一行最近一次提问（旧版本的管理器没有，就写平台与用法），
// 第二行右边是状态：进行中 / 已完成（T-0288）。
// 最上面是“同问”：多模型同问的分组，可以跨管理器，所以不随选中的管理器和平台筛选变（按标题搜索、看已归档的照常）。
// 窄屏没有管理器那一栏（pickManager）：在这里选管理器。只显示和发出事件，不发请求；数据由 LlmChatWorkspace 给。
const props = defineProps<{
  bridges: Bridge[]; managerId: string | null; choices: PlatformChoice[]
  sessions: ChatSession[]; loading: boolean; error: unknown
  groups: ChatGroup[]; selectedGroup: string | null; canCompare: boolean
  filter: SessionFilter; selected: string | null; canCreate: boolean
  syncing: boolean; syncNotice: string; syncError: string
  // 后面还有没显示的会话：load 可以再读一批；records 是已经读到上限，更早的到“会话记录”里找
  more?: 'load' | 'records' | null
  // 选中的管理器连得上吗：checking 还在读状态，off 是已停用（窄屏的管理器选择旁边显示）
  connection?: 'checking' | 'ok' | 'down' | 'off'
  // 窄屏：没有管理器那一栏，在这一栏顶上选
  pickManager?: boolean
}>()
const emit = defineEmits<{ 'update:managerId': [id: string]; 'update:filter': [filter: SessionFilter]; select: [id: string]; create: []; sync: []; retry: []; compare: []; 'open-group': [id: string]; more: [] }>()

const days = computed(() => groupByDay(props.sessions))
const asked = computed(() => { const q = props.filter.q.toLowerCase(); return q ? props.groups.filter(g => g.title.toLowerCase().includes(q)) : props.groups })
const set = (patch: Partial<SessionFilter>) => emit('update:filter', { ...props.filter, ...patch })
const CONNECTION = { checking: '正在检查', ok: '已连接', down: '未连接', off: '已停用' } as const
const archived = computed(() => props.filter.status === 'archived')
// 标题旁的数量：读到的这一批；后面还有就写“+”
const count = computed(() => (props.loading || props.error ? '' : `${props.sessions.length}${props.more ? '+' : ''}`))

// 搜索框：停止输入一小会儿再查，不是每个字都发请求
const q = ref(props.filter.q)
let timer: ReturnType<typeof setTimeout> | undefined
watch(q, value => { clearTimeout(timer); timer = setTimeout(() => { if (value.trim() !== props.filter.q) set({ q: value.trim() }) }, 250) })
watch(() => props.filter.q, value => { if (value !== q.value.trim()) q.value = value })

// “更多”菜单：点外面、按 Esc、选了一项后收起
const menuOpen = ref(false), menuBox = ref<HTMLElement | null>(null)
function outside(event: MouseEvent) { if (menuOpen.value && menuBox.value && !menuBox.value.contains(event.target as Node)) menuOpen.value = false }
function escape(event: KeyboardEvent) { if (event.key === 'Escape') menuOpen.value = false }
document.addEventListener('click', outside, true)
document.addEventListener('keydown', escape)
onBeforeUnmount(() => { clearTimeout(timer); document.removeEventListener('click', outside, true); document.removeEventListener('keydown', escape) })
function pick(action: () => void) { menuOpen.value = false; action() }
function setPlatform(value: string) { set({ platform: (value || null) as Platform | null }) }

const emptyText = computed(() => {
  if (props.filter.q) return '没有标题里带这几个字的会话。'
  if (archived.value) return '没有已归档的会话。'
  return props.filter.platform ? `「${platformName(props.filter.platform)}」上还没有会话。` : '这个 MCP 管理器上还没有会话：点“新建”开始。'
})
// 第二行：最近一次提问；别人建的（别的管理员、智能体、官方程序里已有的）前面写上是谁的——接着发之前要确认，列表上就要看得出来。
// 旧版本的管理器没有提问，写平台、用法和谁建的
function second(s: ChatSession) {
  if (!s.preview) return `${platformName(s.platform)} · ${MODES[s.mode] ?? s.mode} · ${ownerText(s)}`
  return s.owner_kind === 'me' ? s.preview : `${ownerText(s)} · ${s.preview}`
}
</script>

<template>
  <aside class="lc-col-card lc-side" aria-label="会话列表">
    <div v-if="pickManager" class="lc-side-top">
      <div class="lc-manager-head">
        <label class="lc-label" for="lc-manager">MCP 管理器</label>
        <span v-if="connection" class="lc-conn" :data-state="connection">{{ CONNECTION[connection] }}</span>
      </div>
      <select id="lc-manager" :value="managerId ?? ''" @change="emit('update:managerId', ($event.target as HTMLSelectElement).value)">
        <option v-for="b in bridges" :key="b.id" :value="b.id">{{ b.name }}{{ b.enabled ? '' : '（已停用）' }}</option>
      </select>
    </div>
    <div class="lc-pane-head">
      <h2>会话<span v-if="count" class="lc-count">{{ count }}</span></h2>
      <button type="button" class="primary-button lc-new" aria-label="新建会话" :disabled="!canCreate" @click="emit('create')"><Plus aria-hidden="true" />新建</button>
      <div ref="menuBox" class="lc-menu-wrap">
        <button type="button" class="lc-icon" aria-label="会话列表的更多操作" title="更多操作" aria-haspopup="true" :aria-expanded="menuOpen" @click="menuOpen = !menuOpen"><MoreHorizontal aria-hidden="true" /></button>
        <div v-if="menuOpen" class="lc-menu" role="group" aria-label="会话列表的操作">
          <button type="button" :disabled="!canCompare" @click="pick(() => emit('compare'))"><Columns3 aria-hidden="true" />多模型同问</button>
          <button type="button" title="把官方程序里已有的会话同步到列表" :disabled="syncing || !managerId" @click="pick(() => emit('sync'))"><RefreshCw aria-hidden="true" />{{ syncing ? '正在同步…' : '同步官方会话' }}</button>
          <button type="button" :aria-pressed="archived" @click="pick(() => set({ status: archived ? 'active' : 'archived' }))"><component :is="archived ? Undo2 : Archive" aria-hidden="true" />{{ archived ? '回到未归档的会话' : '看已归档的会话' }}</button>
        </div>
      </div>
    </div>
    <div class="lc-side-tools">
      <div class="lc-search">
        <Search aria-hidden="true" />
        <input v-model="q" type="search" maxlength="120" aria-label="按标题搜索会话" placeholder="搜索会话…" />
      </div>
      <select class="lc-filter-select" :value="filter.platform ?? ''" aria-label="按平台筛选" @change="setPlatform(($event.target as HTMLSelectElement).value)">
        <option value="">全部</option>
        <option v-for="c in choices" :key="c.id" :value="c.id">{{ platformName(c.id) }}{{ c.loggedIn ? '' : '（未登录）' }}</option>
      </select>
    </div>
    <p v-if="archived" class="lc-archived-bar" role="status">正在看已归档的会话 <button type="button" class="asv-link" @click="set({ status: 'active' })">回到未归档</button></p>
    <p v-if="syncError" class="lc-note is-red lc-side-note" role="alert">{{ syncError }}</p>
    <p v-else-if="syncNotice" class="lc-note lc-side-note" role="status">{{ syncNotice }}</p>

    <div class="lc-list">
      <section v-if="asked.length" aria-label="同问">
        <h3>同问</h3>
        <ul>
          <li v-for="g in asked" :key="g.id">
            <button type="button" class="lc-item" :aria-current="g.id === selectedGroup ? 'true' : undefined" @click="emit('open-group', g.id)">
              <ChatPlatformIcon group />
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
            <button type="button" class="lc-item" :aria-current="s.session_id === selected ? 'true' : undefined" :title="`${platformName(s.platform)} · ${MODES[s.mode] ?? s.mode} · ${ownerText(s)}建的`" @click="emit('select', s.session_id)">
              <ChatPlatformIcon :platform="s.platform" :mode="s.mode" />
              <span class="lc-item-main">
                <span class="lc-item-top"><strong>{{ s.title || '（没有标题）' }}</strong><time :datetime="new Date(s.updated_at * 1000).toISOString()" :title="seconds(s.updated_at)">{{ listTime(s.updated_at) }}</time></span>
                <span class="lc-item-meta"><span class="lc-item-preview">{{ second(s) }}</span><span class="lc-status" :data-state="sessionState(s).key">{{ sessionState(s).text }}</span></span>
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
  </aside>
</template>
