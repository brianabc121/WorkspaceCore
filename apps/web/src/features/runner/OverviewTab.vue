<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ChevronLeft, ChevronRight, Search } from '@lucide/vue'
import type { Schema } from '../../services/api'
import { useAuthStore } from '../../stores/auth'
import { formatTime } from '../time'
import { LIFETIME, runnerError, STATE, useRunnerEnvironments, type EnvFilter, type EnvFilters as Filters, type EnvSort, type RunnerEnv } from './api'
import { SORT } from './containerMetrics'
import ContainerTable from './ContainerTable.vue'

// 容器总览（T-0186，原“环境”标签合进来）：搜索、按智能体 / 类别 / 状态筛选、排序，服务端分页（每页 50）。
// 统计卡读 overview 的全量；这里显示的是服务端算好的“匹配总数”，不拿当前页的条数冒充全量。
// 筛选条件写在地址里（父组件管），页码只在本页：换筛选、换排序、手动刷新回到第一页；
// 30 秒的自动刷新保留当前页，这一页没了（记录被删或挪到前面）就回到第一页。
const PAGE = 50
const props = defineProps<{ instances: Schema['RunnerInstanceUsage'][]; filters: Filters; workspaceTotal: number | null; selectedId: string | null; recent: Schema['RunnerOverview']['recent']; reload: number }>()
const emit = defineEmits<{ filters: [next: Filters]; select: [id: string, row: HTMLElement | null] }>()
const scope = useAuthStore().workspaceId!

const cursors = ref<(string | undefined)[]>([undefined])
const key = computed(() => JSON.stringify(props.filters))
watch([key, () => props.reload], () => { cursors.value = [undefined] })
const query = computed<EnvFilter>(() => ({ ...props.filters, cursor: cursors.value[cursors.value.length - 1], limit: PAGE }))
const list = useRunnerEnvironments(scope, query)
const data = computed(() => list.data.value)
const items = computed(() => data.value?.items ?? [])
const total = computed(() => (typeof data.value?.total === 'number' ? data.value.total : null))
const page = computed(() => cursors.value.length - 1)
const first = computed(() => page.value * PAGE + 1)
watch(data, (value) => { if (value && !value.items.length && page.value > 0) cursors.value = [undefined] })
function next() { if (data.value?.next_cursor) cursors.value = [...cursors.value, data.value.next_cursor] }
function prev() { if (page.value > 0) cursors.value = cursors.value.slice(0, -1) }

// 搜索框：输入停 300 毫秒再查（服务端搜索），清空立即生效
const term = ref(props.filters.q ?? '')
watch(() => props.filters.q, (q) => { if ((q ?? '') !== term.value.trim()) term.value = q ?? '' })
let timer: ReturnType<typeof setTimeout> | undefined
watch(term, (value) => {
  clearTimeout(timer)
  const q = value.trim().slice(0, 100) || undefined
  timer = setTimeout(() => { if (q !== props.filters.q) emit('filters', { ...props.filters, q }) }, q ? 300 : 0)
})
function set<K extends keyof Filters>(name: K, value: Filters[K]) { emit('filters', { ...props.filters, [name]: value || undefined }) }
const filtered = computed(() => Object.entries(props.filters).some(([k, v]) => k !== 'sort' && v !== undefined && v !== false && v !== ''))
const latest = computed(() => props.recent[0] ?? null)
</script>

<template>
  <section class="ws-panel asv-list cs-list" aria-label="容器列表">
    <div class="ws-toolbar cs-toolbar">
      <label class="ws-search"><Search aria-hidden="true" /><span class="sr-only">搜索容器用途或环境 ID</span><input v-model="term" type="search" placeholder="搜索容器用途或环境 ID" maxlength="100" /></label>
      <select :value="filters.instance_id ?? ''" aria-label="按申请智能体筛选" @change="set('instance_id', ($event.target as HTMLSelectElement).value)">
        <option value="">全部智能体</option>
        <option v-for="i in instances" :key="i.instance_id" :value="i.instance_id">{{ i.name }}</option>
      </select>
      <select :value="filters.lifetime ?? ''" aria-label="按类别筛选" @change="set('lifetime', ($event.target as HTMLSelectElement).value as RunnerEnv['lifetime'])">
        <option value="">全部类别</option>
        <option v-for="(name, k) in LIFETIME" :key="k" :value="k">{{ name }}</option>
      </select>
      <select :value="filters.state ?? ''" aria-label="按状态筛选" @change="set('state', ($event.target as HTMLSelectElement).value as RunnerEnv['state'])">
        <option value="">全部状态</option>
        <option v-for="(name, k) in STATE" :key="k" :value="k">{{ name }}</option>
      </select>
      <select :value="filters.sort ?? 'last_used'" aria-label="排序" @change="set('sort', ($event.target as HTMLSelectElement).value as EnvSort)">
        <option v-for="(name, k) in SORT" :key="k" :value="k">按{{ name }}</option>
      </select>
      <label class="check-field cs-deleted"><input type="checkbox" :checked="!!filters.include_deleted" @change="set('include_deleted', ($event.target as HTMLInputElement).checked)" /> 含已删除</label>
      <p class="cs-scope" role="status">
        <template v-if="total !== null">{{ filtered ? '筛选结果' : '当前工作空间' }} {{ total }} 个<template v-if="filtered && workspaceTotal !== null"> / 工作空间 {{ workspaceTotal }} 个</template></template>
        <template v-else-if="data">已加载 {{ items.length }} 个，总量未提供</template>
      </p>
    </div>
    <p v-if="filters.include_deleted" class="form-help cs-help">列表包含已删除的记录；上面的统计卡不含已删除。</p>
    <p v-if="filters.sort && filters.sort !== 'last_used'" class="form-help cs-help">按实时读数从高到低；过期、未知、未运行的排在最后。</p>

    <p v-if="list.isPending.value" role="status" class="ws-state-line">正在加载容器…</p>
    <div v-else-if="list.error.value && !data" class="ws-state-line"><p role="alert">{{ runnerError(list.error.value) }}</p><button type="button" class="secondary-button" @click="list.refetch()">重试</button></div>
    <p v-else-if="!items.length" class="ws-state-line">{{ filtered ? '没有符合条件的容器。' : '还没有容器环境。打开 OpenClaw 智能体的“运行环境权限”后，智能体会按需申请。' }}</p>
    <ContainerTable v-else :items="items" :selected-id="selectedId" @select="(id, row) => emit('select', id, row)" />

    <div v-if="items.length" class="cs-foot">
      <p class="asv-foot">
        <template v-if="total !== null">显示 {{ first }}–{{ first + items.length - 1 }}，共 {{ total }} 个</template>
        <template v-else>已加载 {{ items.length }} 个（旧版服务端没有提供总数与分页）</template>
        <template v-if="list.error.value"> · 刷新失败，显示的是上次取回的数据</template>
      </p>
      <div v-if="total !== null && (page > 0 || data?.next_cursor)" class="cs-pager">
        <button type="button" class="secondary-button" :disabled="page === 0 || list.isFetching.value" @click="prev"><ChevronLeft aria-hidden="true" />上一页</button>
        <button type="button" class="secondary-button" :disabled="!data?.next_cursor || list.isFetching.value" @click="next">下一页<ChevronRight aria-hidden="true" /></button>
      </div>
    </div>
    <details v-if="recent.length" class="cs-recent">
      <summary>最近回收：<span class="preserve-text">{{ latest!.purpose }}</span> {{ STATE[latest!.state as RunnerEnv['state']] ?? latest!.state }}<template v-if="latest!.reason">（{{ latest!.reason }}）</template></summary>
      <ul class="im-contact-list" aria-label="最近的回收">
        <li v-for="item in recent" :key="`${item.environment_id}:${item.at}`">
          <span>{{ formatTime(String(item.at)) }} · <span class="badge">{{ STATE[item.state as RunnerEnv['state']] ?? item.state }}</span> · <span class="preserve-text">{{ item.purpose }}</span> · {{ item.reason }}</span>
        </li>
      </ul>
      <p class="form-help">回收按规则自动进行：空闲到点先停止、再归档（常驻先做加密快照）；删除条件触发后有宽限期；资源紧张时按保留分先回收分低的。被保护的环境不会被自动停止、归档或回收。</p>
    </details>
  </section>
</template>
