<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter, type LocationQuery } from 'vue-router'
import { useQueryClient } from '@tanstack/vue-query'
import { ArrowLeftRight, ArrowRight, Bot, CircleAlert, CircleCheck, Plus, RefreshCw, Search, Server } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { ApiError } from '../../services/api'
import { useMediaQuery } from '../../composables/useMediaQuery'
import ManagementPage from '../../components/management/ManagementPage.vue'
import ActionFeedback from '../../components/ActionFeedback.vue'
import DrawerDetail from '../../components/DrawerDetail.vue'
import ModalPanel from '../../components/ModalPanel.vue'
import { useAgentAction } from '../agents/action'
import SecretsWorkspace from '../agents/SecretsWorkspace.vue'
import { FRESH_SECONDS, detailNumber, fetchModelIds, impactOf, isCode, mcError, unique, useCapabilities, useConnections, useMigrationEntries, useSummary, type Connection, type ConnectionFilters, type CreatePrefill, type DrawerTab, type Impact } from './api'
import ConnectionTable from './ConnectionTable.vue'
import ConnectionDrawer from './ConnectionDrawer.vue'
import MigrationPanel from './MigrationPanel.vue'
import AssignmentCard from './AssignmentCard.vue'
import './model-connections.css'

// 中转站管理（T-0206 / MC-6，效果图 01；规范第 4 节、契约第 1、2 节）。
// - 汇总卡来自 GET /summary（整个工作空间，不受筛选与分页影响），不用当前页条数代替；“检测通过”分出推理检测。
// - 页签：全部连接 / 官方直连 / 第三方中转（type 筛选）与“迁移与兼容凭据”；搜索（名称、URL、模型 ID）与状态筛选都在服务端，
//   换条件就是另一条查询、从第一页开始，“共 N 条连接”取服务端 total。
// - 地址：?tab=official|relay|compat、?connection=<id> 打开详情（不在当前页也能打开）；兼容凭据沿用 ?secret= 与 ?view=accesses。
// - 详情宽屏在右侧一栏，1440 以下放进右侧抽屉（窄屏全屏）；Esc 关闭，焦点回到打开它的那一行。
// - 停用：第一次 409 CONNECTION_IN_USE 带影响范围，确认后带 confirm_impact 再提交；删除先确认，409 说明原因。
type Tab = 'all' | 'official' | 'relay' | 'compat'
const TABS: { key: Tab; name: string }[] = [{ key: 'all', name: '全部连接' }, { key: 'official', name: '官方直连' }, { key: 'relay', name: '第三方中转' }, { key: 'compat', name: '迁移与兼容凭据' }]
const STATE_FILTERS = { ready: '配置完整', draft: '草稿 / 待完善', needs_review: '需复核', enabled: '已启用', disabled: '已停用' } as const
const CHECK_FILTERS = { passed: '检测通过', failed: '检测失败', rate_limited: '服务商限流', unsupported: '需手动配置模型', unknown: '结果未知', never: '未检测' } as const

const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id
const route = useRoute(), router = useRouter(), cache = useQueryClient()
const wide = typeof window.matchMedia === 'function' ? useMediaQuery('(min-width: 1440px)') : ref(false)
const capabilities = useCapabilities(scope)
const freshSeconds = computed(() => capabilities.data.value?.limits?.check_fresh_seconds ?? FRESH_SECONDS)

const tab = computed<Tab>(() => (['official', 'relay', 'compat'].includes(String(route.query.tab)) ? (route.query.tab as Tab) : 'all'))
const selected = computed(() => (typeof route.query.connection === 'string' && route.query.connection ? route.query.connection : null))
function update(patch: Record<string, string | undefined>, push = false) {
  const query: LocationQuery = { ...route.query }
  for (const [k, v] of Object.entries(patch)) { if (v === undefined || v === '') delete query[k]; else query[k] = v }
  void (push ? router.push({ query }) : router.replace({ query }))
}
function setTab(key: Tab) { update({ tab: key === 'all' ? undefined : key, ...(key !== 'compat' ? { secret: undefined, view: undefined } : {}) }) }
const tablist = ref<HTMLElement | null>(null)
async function tabKey(event: KeyboardEvent) {
  const index = TABS.findIndex(t => t.key === tab.value)
  const next = event.key === 'ArrowRight' ? index + 1 : event.key === 'ArrowLeft' ? index - 1 : event.key === 'Home' ? 0 : event.key === 'End' ? TABS.length - 1 : -2
  if (next === -2) return
  event.preventDefault()
  const target = (next + TABS.length) % TABS.length
  setTab(TABS[target]!.key)
  // 地址栏更新是异步的，aria-selected 可能还没变：按位置把焦点移到目标页签
  await nextTick()
  tablist.value?.querySelectorAll<HTMLElement>('[role="tab"]')[target]?.focus()
}

// ---- 汇总与迁移提示 ----
const summary = useSummary(scope)
const s = computed(() => summary.data.value ?? null)
const value = (n: number | undefined) => (n === undefined ? '—' : String(n))
const migration = useMigrationEntries(scope, () => null, 1)
const counts = computed(() => migration.data.value?.pages[0]?.counts ?? {})
const pending = computed(() => (counts.value.needs_configuration ?? 0) + (counts.value.needs_classification ?? 0) + (counts.value.needs_review ?? 0))
const pendingText = computed(() => [
  counts.value.needs_configuration ? `${counts.value.needs_configuration} 项待补全连接信息` : '',
  counts.value.needs_classification ? `${counts.value.needs_classification} 项待归类` : '',
  counts.value.needs_review ? `${counts.value.needs_review} 项需复核` : '',
].filter(Boolean).join('、'))

// ---- 列表：搜索停顿 300 毫秒再查；空白不发 q（服务端要求 1–100 个字符） ----
const search = ref(''), q = ref(''), stateFilter = ref<'' | keyof typeof STATE_FILTERS>(''), checkFilter = ref<'' | keyof typeof CHECK_FILTERS>('')
let timer: ReturnType<typeof setTimeout> | undefined
watch(search, v => { clearTimeout(timer); timer = setTimeout(() => { q.value = v.trim().slice(0, 100) }, 300) })
onBeforeUnmount(() => clearTimeout(timer))
const filters = computed<ConnectionFilters>(() => {
  const f: ConnectionFilters = {}
  if (q.value) f.q = q.value
  if (tab.value === 'official' || tab.value === 'relay') f.type = tab.value
  if (stateFilter.value === 'enabled' || stateFilter.value === 'disabled') f.enabled = stateFilter.value === 'enabled'
  else if (stateFilter.value) f.configuration_state = stateFilter.value
  if (checkFilter.value) f.check_status = checkFilter.value
  return f
})
const filtered = computed(() => !!(filters.value.q || filters.value.enabled !== undefined || filters.value.configuration_state || filters.value.check_status))
const list = useConnections(scope, () => filters.value)
const items = computed(() => unique(list.data.value?.pages))
const total = computed(() => list.data.value?.pages[0]?.total)

// ---- 详情（右侧一栏 / 抽屉） ----
const creating = ref<CreatePrefill | null>(null)
const intent = ref<'check' | null>(null), drawerTab = ref<DrawerTab | undefined>(undefined)
const drawerKey = ref(0)
const drawerOpen = computed(() => !!selected.value || !!creating.value)
let opener: HTMLElement | null = null
function select(id: string, row: HTMLElement | null = null, next: { intent?: 'check' | null; tab?: DrawerTab } = {}) {
  opener = row
  creating.value = null
  intent.value = next.intent ?? null; drawerTab.value = next.tab
  drawerKey.value++
  if (id !== selected.value || route.query.secret) update({ connection: id, secret: undefined }, true)
}
function openCreate(prefill: CreatePrefill = {}) {
  opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
  creating.value = prefill
  drawerKey.value++
  if (selected.value || route.query.secret) update({ connection: undefined, secret: undefined })
}
async function closeDrawer() {
  const id = selected.value
  creating.value = null; intent.value = null; drawerTab.value = undefined
  if (id) update({ connection: undefined })
  const back = opener
  opener = null
  await nextTick()
  if (back?.isConnected) (back.matches('tr') ? back.querySelector<HTMLElement>('button') : back)?.focus()
  else if (id) document.querySelector<HTMLElement>(`tr[data-connection="${CSS.escape(id)}"] button`)?.focus()
}
function created(id: string) {
  creating.value = null
  drawerKey.value++
  update({ connection: id }, true)
}
// 兼容凭据里打开了一个密钥：收起连接详情，抽屉同一时间只放一个
watch(() => route.query.secret, secret => { if (secret && (selected.value || creating.value)) { creating.value = null; update({ connection: undefined }) } })

// ---- 行操作：停用 / 启用、复制、删除 ----
// 409 CONNECTION_IN_USE 由下面的确认框说明影响范围，不再另报一条笼统的错误
const { busy, error, notice, failure, act, header } = useAgentAction(scope, e => (isCode(e, 'CONNECTION_IN_USE') ? '' : mcError(e)))
const impactDialog = ref<{ connection: Connection; impact: Impact } | null>(null)
async function toggle(c: Connection, confirmed = false) {
  impactDialog.value = null
  const enabling = !c.enabled
  const ok = await act(() => auth.api.PATCH('/api/v1/model-connections/{connection_id}', { params: { header, path: { connection_id: c.id } }, body: { version: c.version, enabled: enabling, confirm_impact: confirmed } }), enabling ? `「${c.name}」已启用。` : `「${c.name}」已停用：后续不能再新建绑定或取用它的 Key；已在智能体进程里的 Key 不保证立即失效。`)
  if (ok || enabling) return
  if (!isCode(failure.value, 'CONNECTION_IN_USE')) return
  const impact = impactOf(failure.value)
  if (impact) impactDialog.value = { connection: c, impact }
  else error.value = mcError(failure.value)
}
async function copy(c: Connection) {
  let models: string[] = []
  try { models = c.model_scope === 'selected' && c.model_count ? await fetchModelIds(scope, c.id) : [] } catch { models = [] }
  openCreate({ name: `${c.name} 副本`.slice(0, 64), description: c.description, connection_type: c.connection_type ?? 'relay', official_provider: c.official_provider, protocol: c.protocol, base_url: c.base_url, model_scope: c.model_scope, models })
}
const removing = ref<Connection | null>(null), removeProblem = ref<{ impact: Impact | null; legacy: number } | null>(null)
function askRemove(c: Connection) { removing.value = c; removeProblem.value = null; error.value = ''; notice.value = '' }
function cancelRemove() { if (!busy.value) { removing.value = null; removeProblem.value = null; error.value = '' } }
async function remove() {
  const c = removing.value
  if (!c) return
  const ok = await act(() => auth.api.DELETE('/api/v1/model-connections/{connection_id}', { params: { header, path: { connection_id: c.id }, query: { version: c.version } } }), `连接「${c.name}」已删除。`)
  if (ok) {
    removing.value = null
    if (selected.value === c.id) update({ connection: undefined })
    return
  }
  if (isCode(failure.value, 'CONNECTION_IN_USE')) removeProblem.value = { impact: impactOf(failure.value), legacy: detailNumber(failure.value, 'legacy_grants') }
}

const refreshing = computed(() => list.isFetching.value || summary.isFetching.value)
function refresh() {
  void cache.invalidateQueries({ predicate: query => query.queryKey[0] === 'agents' && query.queryKey[1] === user && query.queryKey[2] === scope && ['model-connections', 'secrets', 'secret', 'accesses', 'model-options', 'connection-models', 'model-binding'].includes(String(query.queryKey[3])) })
}
const describeList = (e: unknown) => (e instanceof ApiError ? mcError(e) : '请求失败，请稍后重试。')
</script>

<template>
  <ManagementPage class="mc-page" title="中转站管理" description="官方 API 与第三方中转统一配置，供 OpenClaw 智能体使用。只有 owner 可以管理。">
    <template #actions>
      <button type="button" class="secondary-button" :disabled="refreshing" @click="refresh"><RefreshCw aria-hidden="true" />刷新</button>
      <button type="button" class="primary-button" @click="openCreate()"><Plus aria-hidden="true" />添加连接</button>
    </template>

    <div class="asv-layout mc-layout" :class="{ 'has-side': drawerOpen && wide }">
      <div class="asv-main">
        <p v-if="pending > 0" class="mc-banner" role="status">
          <ArrowLeftRight aria-hidden="true" /><span>旧密钥已导入 · {{ pendingText }}</span>
          <button type="button" class="link-button mc-link" @click="setTab('compat')">查看迁移记录<ArrowRight aria-hidden="true" /></button>
        </p>

        <section class="ag-summary" aria-label="连接统计">
          <dl class="ag-tiles mc-tiles">
            <div class="ag-tile tone-violet"><dt><Server aria-hidden="true" />连接总数</dt><dd>{{ value(s?.total) }}</dd></div>
            <div class="ag-tile tone-green"><dt><CircleCheck aria-hidden="true" />检测通过</dt><dd>{{ value(s?.check_passed) }}<small v-if="s" class="mc-tile-note">推理 {{ s.check_passed_inference }} · 元数据 {{ s.check_passed - s.check_passed_inference }}</small></dd></div>
            <div class="ag-tile tone-amber"><dt><CircleAlert aria-hidden="true" />待完善</dt><dd>{{ value(s?.needs_attention) }}</dd></div>
            <div class="ag-tile tone-violet"><dt><Bot aria-hidden="true" />关联智能体</dt><dd>{{ value(s?.unique_granted_instances) }}</dd></div>
          </dl>
          <p v-if="summary.error.value" class="form-help" role="alert">统计读取失败：{{ describeList(summary.error.value) }}</p>
          <p v-else-if="s" class="form-help">整个工作空间的统计，不受筛选影响。检测通过：启用、配置完整且 15 分钟内对当前配置检测通过（元数据检测不代表模型能推理）；待完善：待补全、待归类、需复核或最近检测失败 / 限流。<template v-if="s.non_model">另有 {{ s.non_model }} 个非模型凭据。</template></p>
        </section>

        <ActionFeedback :error="removing || impactDialog ? '' : error" :notice="notice" />

        <div ref="tablist" class="sk-tabs mc-page-tabs" role="tablist" aria-label="连接分类">
          <button v-for="t in TABS" :key="t.key" type="button" role="tab" :aria-selected="tab === t.key" :tabindex="tab === t.key ? 0 : -1" @click="setTab(t.key)" @keydown="tabKey">{{ t.name }}</button>
        </div>

        <div v-if="tab !== 'compat'" role="tabpanel" :aria-label="TABS.find(t => t.key === tab)?.name" class="mc-panel-stack">
          <section class="ws-panel asv-list" aria-label="连接列表">
            <div class="ws-toolbar">
              <label class="ws-search"><Search aria-hidden="true" /><span class="sr-only">搜索名称、URL 或模型</span><input v-model="search" type="search" placeholder="搜索名称、URL 或模型" maxlength="100" /></label>
              <select v-model="stateFilter" aria-label="按状态筛选"><option value="">全部状态</option><option v-for="(label, key) in STATE_FILTERS" :key="key" :value="key">{{ label }}</option></select>
              <select v-model="checkFilter" aria-label="按检测结果筛选"><option value="">全部检测结果</option><option v-for="(label, key) in CHECK_FILTERS" :key="key" :value="key">{{ label }}</option></select>
            </div>
            <p v-if="list.isPending.value" role="status" class="ws-state-line">正在加载连接…</p>
            <div v-else-if="list.error.value && !items.length" class="ws-state-line"><p role="alert">{{ describeList(list.error.value) }}</p><button type="button" class="secondary-button" @click="list.refetch()">重试</button></div>
            <p v-else-if="!items.length && (filtered || tab !== 'all')" class="ws-state-line">没有符合条件的连接。</p>
            <p v-else-if="!items.length" class="ws-state-line">还没有连接。点右上角“添加连接”录入官方 API 或第三方中转的地址、Key 与模型。</p>
            <ConnectionTable v-else :items="items" :selected="selected" :fresh-seconds="freshSeconds" @select="select" @check="(c, row) => select(c.id, row, { intent: 'check' })" @toggle="toggle" @copy="copy" @remove="askRemove" />
            <footer v-if="items.length" class="asv-foot ag-foot">
              <span>共 {{ total ?? items.length }} 条连接<template v-if="filtered">（符合当前条件）</template><template v-if="typeof total === 'number' && items.length < total"> · 已显示 {{ items.length }} 条</template><template v-if="list.error.value"> · 刷新失败，显示的是上次取回的数据</template></span>
              <button v-if="list.hasNextPage.value" type="button" class="secondary-button" :disabled="list.isFetchingNextPage.value" @click="list.fetchNextPage()">加载更多连接</button>
            </footer>
          </section>
          <AssignmentCard />
        </div>
        <div v-else role="tabpanel" aria-label="迁移与兼容凭据" class="mc-panel-stack">
          <MigrationPanel @open="id => select(id)" />
          <SecretsWorkspace embedded title="兼容凭据" description="非模型 Token 与旧密钥库里的凭据：轮换、删除与取用记录照旧在这里管理（Runner、旧配置仍在用）。模型服务的 Key 请在连接里更换。" tab-param="view" />
        </div>
      </div>

      <aside v-if="drawerOpen && wide" class="ws-panel asv-side mc-side" :aria-label="selected ? '编辑连接' : '添加连接'" @keydown.esc="closeDrawer">
        <ConnectionDrawer :key="`${selected ?? 'new'}:${drawerKey}`" :connection-id="selected" :capabilities="capabilities.data.value" :prefill="creating" :intent="intent" :initial-tab="drawerTab" aside @close="closeDrawer" @created="created" @copy="openCreate" @toggle="toggle" />
      </aside>
    </div>
    <DrawerDetail v-if="drawerOpen && !wide" :title="selected ? '编辑连接' : '添加连接'" @close="closeDrawer">
      <ConnectionDrawer :key="`${selected ?? 'new'}:${drawerKey}`" :connection-id="selected" :capabilities="capabilities.data.value" :prefill="creating" :intent="intent" :initial-tab="drawerTab" @close="closeDrawer" @created="created" @copy="openCreate" @toggle="toggle" />
    </DrawerDetail>

    <ModalPanel v-if="impactDialog" title="确认停用连接" :busy="busy" @close="impactDialog = null">
      <div class="mc-dialog">
        <p>停用「{{ impactDialog.connection.name }}」会影响下面的智能体：停用后它们不能再取用这条连接的 Key（含旧配置与运行环境的引用），也不能新建绑定；已在进程里的 Key 不保证立即失效，智能体不会被自动停止。</p>
        <ul v-if="impactDialog.impact.instances.length"><li v-for="i in impactDialog.impact.instances" :key="i.instance_id">{{ i.instance_name }}<template v-if="i.bound">（以它为主模型）</template></li></ul>
        <p v-if="impactDialog.impact.bound_instances" class="form-help">{{ impactDialog.impact.bound_instances }} 个智能体以它为主模型<template v-if="impactDialog.impact.pending_jobs">，{{ impactDialog.impact.pending_jobs }} 个配置应用任务还没完成</template>。</p>
        <p v-if="error" class="feedback" role="alert">{{ error }}</p>
        <div class="form-actions"><button type="button" class="primary-button danger-button" :disabled="busy" @click="toggle(impactDialog.connection, true)">{{ busy ? '正在停用…' : '确认停用' }}</button><button type="button" class="secondary-button" :disabled="busy" @click="impactDialog = null">取消</button></div>
      </div>
    </ModalPanel>

    <ModalPanel v-if="removing" title="删除连接" :busy="busy" @close="cancelRemove">
      <div class="mc-dialog">
        <p>删除「{{ removing.name }}」？连接与它保存的 Key 一起删除，不能恢复；不会删除或停止任何智能体。</p>
        <p v-if="removing.grant_count || removing.binding_count" class="form-help">它仍授权给 {{ removing.grant_count }} 个智能体<template v-if="removing.binding_count">，{{ removing.binding_count }} 个以它为主模型</template>：需要先撤销授权、更换主模型，删除才会成功。</p>
        <div v-if="removeProblem" class="mc-callout" role="alert">
          <p><strong>没有删除</strong></p>
          <template v-if="removeProblem.impact && (removeProblem.impact.instances.length || removeProblem.impact.bound_instances || removeProblem.impact.pending_jobs)">
            <p>连接仍有授权、绑定或进行中的应用任务：</p>
            <ul><li v-for="i in removeProblem.impact.instances" :key="i.instance_id">{{ i.instance_name }}<template v-if="i.bound">（以它为主模型）</template></li></ul>
            <p v-if="removeProblem.impact.pending_jobs">{{ removeProblem.impact.pending_jobs }} 个配置应用任务还没完成。</p>
            <p>请先在“智能体授权”里撤销授权、在“OpenClaw 连接分配”里更换主模型。</p>
          </template>
          <p v-else-if="removeProblem.legacy">这条凭据仍被旧配置或运行环境引用（{{ removeProblem.legacy }} 处旧授权）。请先撤销旧授权，或在“迁移与兼容凭据”里归类为非模型凭据保留。</p>
          <p v-else>连接仍在使用中。</p>
        </div>
        <p v-if="error" class="feedback" role="alert">{{ error }}</p>
        <div class="form-actions"><button type="button" class="primary-button danger-button" :disabled="busy" @click="remove">{{ busy ? '正在删除…' : '删除' }}</button><button type="button" class="secondary-button" :disabled="busy" @click="cancelRemove">取消</button></div>
      </div>
    </ModalPanel>
  </ManagementPage>
</template>
