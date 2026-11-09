<script setup lang="ts">
import { computed, nextTick, onScopeDispose, ref, watch } from 'vue'
import { useRoute, useRouter, type LocationQuery } from 'vue-router'
import { useQueryClient } from '@tanstack/vue-query'
import { ArrowUpDown, CalendarClock, Clock, Info, RefreshCw } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { useMediaQuery } from '../../composables/useMediaQuery'
import ManagementPage from '../../components/management/ManagementPage.vue'
import ManagementSection from '../../components/management/ManagementSection.vue'
import DrawerDetail from '../../components/DrawerDetail.vue'
import ModalPanel from '../../components/ModalPanel.vue'
import ApprovalSummaryCards from './ApprovalSummaryCards.vue'
import ApprovalInboxTable from './ApprovalInboxTable.vue'
import ApprovalDetailPanel from './ApprovalDetailPanel.vue'
import ExecutionItem from './ExecutionItem.vue'
import {
  PERIOD_LABELS, RISK_LABELS, STATUS_LABELS, aiError, ownKeys, useApprovalSummary, useExecutions,
  type ExecutionFilter, type ExecutionPeriod, type ExecutionSort, type ExecutionStatus, type RiskLevel, type ScopeFilter,
} from './queries'
import { remainingSeconds, shortId } from './approvalFormat'
import './approval-center.css'

// AI 审批中心（T-0191，效果图 01；规范 docs/v2/ai-admin-redesign §4、契约 §2）：汇总卡、三个视图（?view=：待审批 /
// 全部调用 activity / 执行异常 exceptions）、服务端筛选与排序、待处理表与执行异常区；?id= 打开详情（通知、IM 卡片、
// 旧的 /approvals?id= 都落到这里，记录不在当前列表里也按 ID 读取）。宽屏（1440 以上）详情在右侧一栏，窄屏放进右侧抽屉。
// 刷新只重新读取（从第一页开始），从不批准；规则说明只读，没有批量批准或永久信任。
type View = 'pending' | 'activity' | 'exceptions'
const VIEWS: { key: View; name: string }[] = [{ key: 'pending', name: '待审批' }, { key: 'activity', name: '全部调用' }, { key: 'exceptions', name: '执行异常' }]
const VIEW_HINT: Record<View, string> = {
  pending: '仅需审批的操作进入待办，其他调用见全部调用。',
  activity: '每一次工具调用：读取、知识库、直接执行与已处理的审批都在这里，按发起时间倒序。',
  exceptions: '执行失败或结果未知的调用，按发起时间倒序；结果未知时请到对应页面核对，不会自动重试。',
}
const EXCEPTION_STATUSES: ExecutionStatus[] = ['failed', 'unknown']
const auth = useAuthStore(), route = useRoute(), router = useRouter(), cache = useQueryClient()
const scope = auth.workspaceId!, user = auth.user?.id
const wide = useMediaQuery('(min-width: 1440px)')

const view = computed<View>(() => (route.query.view === 'activity' || route.query.view === 'exceptions' ? route.query.view : 'pending'))
const selected = computed(() => (typeof route.query.id === 'string' && route.query.id ? route.query.id : null))

// 筛选：都交给服务端（契约 §2.2）；变了就是新的查询键，从第一页重新读
const period = ref<ExecutionPeriod>('24h'), instance = ref(''), risk = ref<RiskLevel | ''>(''), tool = ref(''), status = ref<ExecutionStatus | ''>('')
const pendingSort = ref<ExecutionSort>('expires_asc')
const base = computed<ScopeFilter>(() => ({ period: period.value, risk_level: risk.value ? [risk.value] : [], instance_id: instance.value || null, tool_name: tool.value || null }))
const listFilter = computed<ExecutionFilter>(() =>
  view.value === 'pending' ? { ...base.value, status: ['pending_approval'], sort: pendingSort.value }
  : view.value === 'exceptions' ? { ...base.value, status: EXCEPTION_STATUSES, sort: 'created_desc' }
  : { ...base.value, status: status.value ? [status.value] : [], sort: 'created_desc' })
const summary = useApprovalSummary(scope, () => base.value)
const data = computed(() => summary.data.value)
const list = useExecutions(scope, () => listFilter.value)
const items = computed(() => [...new Map((list.data.value?.pages ?? []).flatMap(p => p.items).map(e => [e.id, e])).values()])
// 待审批视图底下的执行异常区：汇总说有异常才读，最多 5 条
const strip = useExecutions(scope, () => ({ ...base.value, status: EXCEPTION_STATUSES, sort: 'created_desc', limit: 5 }), () => view.value === 'pending' && (data.value?.exception_count ?? 0) > 0)
const stripItems = computed(() => (strip.data.value?.pages[0]?.items ?? []))

// 选项：服务端给的可见实例与工具（只随时间范围变，不受其他已选条件影响）。换条件时新汇总回来之前沿用上一份，
// 免得下拉框一时变空、把刚选的值清掉；已选但不在选项里的照样保留
const known = ref<{ instances: { id: string; name: string }[]; tools: string[] }>({ instances: [], tools: [] })
watch(data, (d) => { if (d) known.value = { instances: Array.isArray(d.instances) ? d.instances : [], tools: Array.isArray(d.tools) ? d.tools : [] } }, { immediate: true })
const instanceOptions = computed(() => {
  const options = [...known.value.instances]
  if (instance.value && !options.some(o => o.id === instance.value)) options.push({ id: instance.value, name: `智能体 ${shortId(instance.value)}` })
  return options
})
const toolOptions = computed(() => {
  const options = [...known.value.tools].sort()
  if (tool.value && !options.includes(tool.value)) options.push(tool.value)
  return options
})
const risks = Object.entries(RISK_LABELS) as [RiskLevel, string][]
const statuses = Object.entries(STATUS_LABELS) as [ExecutionStatus, string][]
const periods = Object.entries(PERIOD_LABELS) as [ExecutionPeriod, string][]

// 服务端时钟：本机时间 + (summary.as_of − 收到汇总时的本机时间)，沿用最近一次汇总的偏差。
// 第一次汇总还没回来时不显示倒计时；读不到汇总就按本机时间（到期仍由服务端判定）
const now = ref(Date.now())
const timer = setInterval(() => { now.value = Date.now() }, 1000)
onScopeDispose(() => clearInterval(timer))
const offset = ref<number | null>(null)
watch(data, (d) => { if (d) offset.value = d.offset_ms }, { immediate: true })
const serverNow = computed(() => (offset.value !== null ? now.value + offset.value : summary.error.value ? now.value : null))
const expiringWindow = computed(() => data.value?.expiring_window_seconds ?? 180)

// 列表里的待审批归零：重新读取（列表、汇总、打开的详情），每条只读一次；不在前端改状态
const refreshedAtZero = new Set<string>()
const dueIds = computed(() => (serverNow.value === null ? [] : items.value.filter(e => e.status === 'pending_approval' && remainingSeconds(e.expires_at, serverNow.value!) === 0).map(e => e.id)))
watch(dueIds, (ids) => {
  const fresh = ids.filter(id => !refreshedAtZero.has(id))
  if (!fresh.length) return
  fresh.forEach(id => refreshedAtZero.add(id))
  void cache.invalidateQueries({ predicate: ownKeys(user, scope) })
})

function update(patch: Record<string, string | undefined>, push = false) {
  const query: LocationQuery = { ...route.query }
  for (const [k, v] of Object.entries(patch)) { if (v === undefined || v === '') delete query[k]; else query[k] = v }
  void (push ? router.push({ query }) : router.replace({ query }))
}
function show(next: View) { update({ view: next === 'pending' ? undefined : next }) }
let opener: HTMLElement | null = null
function open(id: string, row: HTMLElement | null = null) { opener = row; update({ id }, true) }
async function close() {
  const id = selected.value
  update({ id: undefined })
  const back = opener
  opener = null
  await nextTick()
  if (back?.isConnected) back.focus()
  else if (id) [...document.querySelectorAll<HTMLElement>('tr[data-execution]')].find(row => row.dataset.execution === id)?.focus()
}

// 刷新：只重新读取。列表从第一页开始，其余（汇总、详情）重新验证；不发任何写请求
const refreshing = computed(() => summary.isFetching.value || list.isFetching.value)
async function refresh() {
  await Promise.all([
    cache.resetQueries({ predicate: q => ownKeys(user, scope)(q) && q.queryKey[3] === 'list' }),
    cache.invalidateQueries({ predicate: q => ownKeys(user, scope)(q) && q.queryKey[3] !== 'list' }),
  ])
}
const rulesOpen = ref(false)

const tabCount = (key: View) => (key === 'pending' ? data.value?.pending_count : key === 'exceptions' ? data.value?.exception_count : undefined)
const listTitle = computed(() => {
  const count = (n: number | undefined) => (n === undefined ? '' : ` · ${n} 项`)
  if (view.value === 'pending') return `待处理请求${count(data.value?.pending_count)}`
  if (view.value === 'exceptions') return `执行异常${count(data.value?.exception_count)}`
  return '全部调用'
})
const loadedText = computed(() => {
  const total = view.value === 'pending' ? data.value?.pending_count : view.value === 'exceptions' ? data.value?.exception_count : undefined
  if (total !== undefined && total !== items.value.length) return `已显示 ${items.value.length} / ${total} 条`
  return view.value === 'activity' ? `已加载 ${items.value.length} 条` : ''
})
const filtered = computed(() => !!(instance.value || risk.value || tool.value || (view.value === 'activity' && status.value)))
const emptyText = computed(() => filtered.value ? '没有符合条件的记录。' : view.value === 'pending' ? '没有待审批的请求。' : view.value === 'exceptions' ? '没有执行异常。' : '还没有工具调用记录。')
</script>

<template>
  <ManagementPage title="AI 审批" description="看清操作影响，逐条审批，跟踪执行结果。">
    <template #context>
      <label class="mg-tag ac-period"><CalendarClock aria-hidden="true" /><span>统计范围</span>
        <select v-model="period"><option v-for="[value, label] in periods" :key="value" :value="value">{{ label }}</option></select>
      </label>
    </template>
    <template #actions>
      <button type="button" class="secondary-button" :disabled="refreshing" @click="refresh"><RefreshCw aria-hidden="true" />刷新</button>
      <button type="button" class="primary-button" @click="rulesOpen = true"><Info aria-hidden="true" />规则说明</button>
    </template>

    <div class="asv-layout ac-layout" :class="{ 'has-side': !!selected && wide }">
      <div class="asv-main">
        <ApprovalSummaryCards :summary="data" :period="period" :loading="summary.isPending.value" :error="summary.error.value" />

        <section class="ws-panel ac-views" aria-label="视图与筛选">
          <div class="ac-tabs" role="tablist" aria-label="审批视图">
            <button v-for="v in VIEWS" :key="v.key" type="button" role="tab" :aria-selected="view === v.key" @click="show(v.key)">
              {{ v.name }} <span v-if="tabCount(v.key) !== undefined" class="ac-count">{{ tabCount(v.key) }}</span>
            </button>
          </div>
          <p class="ac-hint"><Info aria-hidden="true" />{{ VIEW_HINT[view] }}</p>
          <section class="ws-toolbar ac-filters" aria-label="筛选">
            <label><span class="sr-only">智能体</span><select v-model="instance"><option value="">全部智能体</option><option v-for="o in instanceOptions" :key="o.id" :value="o.id">{{ o.name }}</option></select></label>
            <label><span class="sr-only">类型</span><select v-model="risk"><option value="">全部类型</option><option v-for="[value, label] in risks" :key="value" :value="value">{{ label }}</option></select></label>
            <label><span class="sr-only">工具</span><select v-model="tool"><option value="">全部工具</option><option v-for="name in toolOptions" :key="name" :value="name">{{ name }}</option></select></label>
            <label v-if="view === 'activity'"><span class="sr-only">状态</span><select v-model="status"><option value="">全部状态</option><option v-for="[value, label] in statuses" :key="value" :value="value">{{ label }}</option></select></label>
            <label v-if="view === 'pending'" class="ac-sort"><ArrowUpDown aria-hidden="true" /><span class="sr-only">排序</span><select v-model="pendingSort"><option value="expires_asc">按到期时间</option><option value="created_desc">按发起时间</option></select></label>
            <span v-else class="ac-sort-text"><ArrowUpDown aria-hidden="true" />按发起时间</span>
          </section>
          <p v-if="!auth.canManage" class="mg-hint"><Info aria-hidden="true" />你只能看到以自己身份执行的记录；审批由 owner 或 admin 处理。</p>
        </section>

        <div role="tabpanel" :aria-label="VIEWS.find(v => v.key === view)?.name" class="ac-panel">
          <ManagementSection :title="listTitle">
            <template v-if="view === 'pending' && pendingSort === 'expires_asc'" #actions><span class="ac-tag is-soon"><Clock aria-hidden="true" />即将到期优先</span></template>
            <p v-if="list.isPending.value" role="status" class="ws-state-line">正在加载…</p>
            <div v-else-if="list.error.value && !items.length" class="ws-state-line"><p role="alert">{{ aiError(list.error.value) }}</p><button type="button" class="secondary-button" @click="list.refetch()">重试</button></div>
            <p v-else-if="!items.length" class="ws-state-line">{{ emptyText }}</p>
            <ApprovalInboxTable v-else :items="items" :view="view" :selected-id="selected" :server-now="serverNow" :expiring-window="expiringWindow" @select="open" />
            <p v-if="list.error.value && items.length" role="alert" class="ac-error">{{ aiError(list.error.value) }}</p>
            <button v-if="list.hasNextPage.value" type="button" class="secondary-button ac-more" :disabled="list.isFetchingNextPage.value" @click="list.fetchNextPage()">加载更多</button>
            <div class="ac-foot">
              <p v-if="view === 'pending'" class="ac-foot-note"><Clock aria-hidden="true" />待审批请求 15 分钟后失效，过期后不可批准。</p>
              <span v-if="loadedText" class="ac-foot-count">{{ loadedText }}</span>
            </div>
          </ManagementSection>
        </div>

        <ManagementSection v-if="view === 'pending' && stripItems.length" :title="`执行异常 · ${data?.exception_count ?? stripItems.length}`">
          <template #actions><button type="button" class="asv-link" @click="show('exceptions')">查看全部</button></template>
          <ul class="ac-exceptions"><ExecutionItem v-for="e in stripItems" :key="e.id" :execution="e" :selected="selected === e.id" @open="open($event)" /></ul>
        </ManagementSection>
      </div>
      <div v-if="selected && wide" class="ws-panel asv-side ac-side">
        <ApprovalDetailPanel :key="selected" :scope="scope" :execution-id="selected" :server-now="serverNow" :expiring-window="expiringWindow" aside @close="close" />
      </div>
    </div>
    <DrawerDetail v-if="selected && !wide" title="审批详情" @close="close">
      <ApprovalDetailPanel :key="selected" :scope="scope" :execution-id="selected" :server-now="serverNow" :expiring-window="expiringWindow" @close="close" />
    </DrawerDetail>

    <ModalPanel v-if="rulesOpen" title="审批规则说明" @close="rulesOpen = false">
      <div class="ac-rules">
        <p>规则由工作台服务端执行，这里只是说明，不能在页面上修改。</p>
        <ul>
          <li><strong>哪些要审批：</strong>写操作与高危操作在这里逐条审批后才执行。读取、知识库与直接执行的工具，以及管理员已为实例打开的授权例外（领取 / 完成任务、项目管理、IM 发消息、Issue、发送邮件、运行环境等），按工作台的权限与限额直接执行，不进入待审批；它们的记录在“全部调用”里查看。</li>
          <li><strong>有效期：</strong>待审批请求发起后 15 分钟失效，到期由服务端判定；过期后不能批准，也不能延长，需要的话请让智能体重新发起。</li>
          <li><strong>高危操作：</strong>批准时要重新输入你的工作台登录密码；拒绝不需要密码。</li>
          <li><strong>审批只授权这一次操作：</strong>按实例发起时存下的固定参数、以实例登记的执行身份执行一次；参数在这里只读，不能修改。</li>
          <li><strong>没有批量批准或永久信任：</strong>每条请求单独处理；在 IM 里回复“同意”不算批准。</li>
          <li><strong>谁能审批：</strong>owner 与 admin；成员与访客只能查看以自己身份执行的记录。</li>
          <li><strong>批准不等于成功：</strong>执行结果（执行中 / 成功 / 失败 / 结果未知）如实显示；结果未知时请到对应页面核对，工作台不会自动重试。</li>
        </ul>
        <div class="form-actions"><button type="button" class="secondary-button" @click="rulesOpen = false">知道了</button></div>
      </div>
    </ModalPanel>
  </ManagementPage>
</template>
