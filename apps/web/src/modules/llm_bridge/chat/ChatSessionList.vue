<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { Plus, RefreshCw, Search } from '@lucide/vue'
import DataState from '../../../components/DataState.vue'
import { MODES, platformName, seconds, type Bridge, type Platform } from '../api'
import { groupByDay, ownerText, type ChatSession, type PlatformChoice, type SessionFilter } from './chatApi'

// 左栏：选 MCP 管理器、新建、按标题搜索、按平台筛选，会话按“今天 / 昨天 / 更早”分组。
// 只显示和发出事件，不发请求；数据由 LlmChatWorkspace 给。
const props = defineProps<{
  bridges: Bridge[]; managerId: string | null; choices: PlatformChoice[]
  sessions: ChatSession[]; loading: boolean; error: unknown
  filter: SessionFilter; selected: string | null; canCreate: boolean
  syncing: boolean; syncNotice: string; syncError: string
}>()
const emit = defineEmits<{ 'update:managerId': [id: string]; 'update:filter': [filter: SessionFilter]; select: [id: string]; create: []; sync: []; retry: [] }>()

const MARK: Record<string, string> = { chatgpt: 'G', codex: 'Cx', claude: 'Cl', workbuddy: 'W' }
const groups = computed(() => groupByDay(props.sessions))
const set = (patch: Partial<SessionFilter>) => emit('update:filter', { ...props.filter, ...patch })

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
      <label class="lc-label" for="lc-manager">MCP 管理器</label>
      <select id="lc-manager" :value="managerId ?? ''" @change="emit('update:managerId', ($event.target as HTMLSelectElement).value)">
        <option v-for="b in bridges" :key="b.id" :value="b.id">{{ b.name }}{{ b.enabled ? '' : '（已停用）' }}</option>
      </select>
      <button type="button" class="primary-button lc-new" :disabled="!canCreate" @click="emit('create')"><Plus aria-hidden="true" />新建会话</button>
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
      <DataState v-if="loading || error" :loading="loading" :error="error" :empty="false" empty-text="" @retry="emit('retry')" />
      <p v-else-if="!sessions.length" class="lc-none">{{ emptyText }}</p>
      <section v-for="g in groups" v-else :key="g.name" :aria-label="g.name">
        <h3>{{ g.name }}</h3>
        <ul>
          <li v-for="s in g.items" :key="s.session_id">
            <button type="button" class="lc-item" :aria-current="s.session_id === selected ? 'true' : undefined" @click="emit('select', s.session_id)">
              <span class="lc-mark" :data-platform="s.platform" aria-hidden="true">{{ MARK[s.platform] ?? '?' }}</span>
              <span class="lc-item-main">
                <strong>{{ s.title || '（没有标题）' }}</strong>
                <span class="lc-item-meta">{{ platformName(s.platform) }} · {{ MODES[s.mode] ?? s.mode }} · {{ ownerText(s) }}</span>
                <span class="lc-item-meta"><time>{{ seconds(s.updated_at) }}</time><span v-if="s.busy" class="lc-busy">回答中</span></span>
              </span>
            </button>
          </li>
        </ul>
      </section>
    </div>

    <div class="lc-side-foot">
      <label class="check-field"><input type="checkbox" :checked="filter.status === 'archived'" @change="set({ status: ($event.target as HTMLInputElement).checked ? 'archived' : 'active' })" />看已归档的</label>
      <button type="button" class="asv-link" :disabled="syncing || !managerId" @click="emit('sync')"><RefreshCw aria-hidden="true" />{{ syncing ? '正在同步…' : '同步官方程序里的会话' }}</button>
      <p v-if="syncError" class="lc-note is-red" role="alert">{{ syncError }}</p>
      <p v-else-if="syncNotice" class="lc-note" role="status">{{ syncNotice }}</p>
    </div>
  </aside>
</template>
