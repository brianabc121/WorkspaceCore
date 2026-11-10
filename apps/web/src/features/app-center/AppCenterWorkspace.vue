<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { useRoute, useRouter, type LocationQuery } from 'vue-router'
import { BookOpen, CircleCheck, Layers, LayoutGrid, Plus, Puzzle, RefreshCw, Search, TriangleAlert } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { useNavigationStore } from '../../stores/navigation'
import { errorMessage } from '../../services/api'
import { useMediaQuery } from '../../composables/useMediaQuery'
import { modules as definitions } from '../../modules/registry'
import { hasRole, type CustomPage } from '../../app/sidebar'
import ManagementPage from '../../components/management/ManagementPage.vue'
import ActionFeedback from '../../components/ActionFeedback.vue'
import DrawerDetail from '../../components/DrawerDetail.vue'
import ModalPanel from '../../components/ModalPanel.vue'
import { actionError, useAppCenterActions, useManagement, type Management } from './api'
import { STATUS_FILTERS, SYNC_REASONS, buildAppCenterViewModel, filterModules, type ModuleView, type StatusFilter } from './viewModel'
import ModuleCard from './ModuleCard.vue'
import ModuleDetail, { type DetailTab } from './ModuleDetail.vue'
import ModuleActivity from './ModuleActivity.vue'
import CustomPagesPanel from './CustomPagesPanel.vue'
import ModuleGuide from './ModuleGuide.vue'
import PageEntryEditor from './PageEntryEditor.vue'
import '../agents/secrets.css'
import './app-center.css'

// 应用中心（T-0211，效果图 docs/v2/app-center/assets/01-app-center.png；方案第 3 节）。
// - 汇总：已接入模块、已启用、需处理来自后端（完整集合，不受搜索筛选影响）；自定义页面是当前账号看得到的入口数。
// - 页签：功能模块 / 自定义页面 / 操作记录 / 接入指南；地址 ?tab=…&module=<key> 可以直接打开某个模块的详情。
// - 启用、部署、健康、同步四类状态分开；列表只读缓存，“检测连接”在详情里显式触发。
// - owner 启停与重试；admin 只读（可以检测）。启停带 state_version，冲突、忙、结果未知时先重新读取真实状态，不自动重发。
// - 详情宽屏在右侧一栏，1440 以下放进右侧抽屉；Esc 关闭，焦点回到打开它的按钮。
type Tab = 'modules' | 'pages' | 'activity' | 'guide'
const TAB_KEYS: Tab[] = ['modules', 'pages', 'activity', 'guide']

const auth = useAuthStore(), scope = auth.workspaceId!
const navigation = useNavigationStore()
const route = useRoute(), router = useRouter()
const wide = typeof window.matchMedia === 'function' ? useMediaQuery('(min-width: 1440px)') : ref(false)
const management = useManagement(scope)
const actions = useAppCenterActions(scope)

const tab = computed<Tab>(() => (TAB_KEYS.includes(route.query.tab as Tab) ? (route.query.tab as Tab) : 'modules'))
const selected = computed(() => (typeof route.query.module === 'string' && route.query.module ? route.query.module : null))
function update(patch: Record<string, string | undefined>, push = false) {
  const query: LocationQuery = { ...route.query }
  for (const [k, v] of Object.entries(patch)) { if (v === undefined || v === '') delete query[k]; else query[k] = v }
  void (push ? router.push({ query }) : router.replace({ query }))
}
function setTab(key: Tab) { update({ tab: key === 'modules' ? undefined : key }) }

// ---- 数据 ----
const data = computed(() => management.data.value ?? null)
const vm = computed(() => buildAppCenterViewModel(data.value?.items ?? [], definitions))
const names = computed(() => new Map(vm.value.modules.map(view => [view.key, view.name])))
const pages = computed(() => (navigation.layout?.pages ?? []).filter(page => hasRole(auth.currentWorkspace?.role, page.min_role)))
const canEditPages = computed(() => !!navigation.layout?.can_manage)
const value = (n: number | undefined) => (n === undefined ? '—' : String(n))

// ---- 列表：搜索与状态筛选只影响列表，汇总不变 ----
const search = ref(''), status = ref<StatusFilter>('all')
const shown = computed(() => filterModules(vm.value.modules, search.value, status.value))

// ---- 详情 ----
const detailTab = ref<DetailTab | undefined>(undefined)
const current = computed(() => vm.value.modules.find(view => view.key === selected.value) ?? null)
let opener: HTMLElement | null = null
function openDetails(view: ModuleView, from: HTMLElement | null = null, next?: DetailTab) {
  opener = from
  detailTab.value = next
  if (view.key !== selected.value) update({ module: view.key }, true)
}
async function closeDetails() {
  const key = selected.value
  detailTab.value = undefined
  if (key) update({ module: undefined })
  const back = opener
  opener = null
  await nextTick()
  if (back?.isConnected) back.focus()
  else if (key) document.querySelector<HTMLElement>(`[data-module="${CSS.escape(key)}"] .apc-details`)?.focus()
}

// ---- 写操作：一次一个 ----
const busy = ref<{ key: string; action: 'toggle' | 'retry' | 'check' } | null>(null)
const error = ref(''), notice = ref('')
const disabling = ref<ModuleView | null>(null)
const busyFor = (key: string) => (busy.value?.key === key ? busy.value.action : null)

async function run(view: ModuleView, action: 'toggle' | 'retry' | 'check', effect: (item: Management) => Promise<string>) {
  if (busy.value || !actions.active()) return
  busy.value = { key: view.key, action }; error.value = ''; notice.value = ''
  try {
    const message = await effect(view.item)
    if (actions.active()) notice.value = message
  } catch (cause) {
    if (actions.active()) error.value = actionError(cause).message
  } finally {
    busy.value = null
    // 成功、冲突、忙或结果未知都重新读取真实状态（不自动重发）
    await actions.refresh()
  }
}
function requestToggle(view: ModuleView) {
  if (view.item.module.enabled) { disabling.value = view; return }
  void toggle(view, true)
}
async function toggle(view: ModuleView, enabled: boolean) {
  disabling.value = null
  await run(view, 'toggle', async item => {
    await actions.toggle(item, enabled)
    return `「${view.name}」已${enabled ? '启用' : '停用'}。`
  })
}
async function retry(view: ModuleView) {
  await run(view, 'retry', async item => {
    const result = await actions.reconcile(item)
    const sync = result.sync
    if (sync.state === 'succeeded') return `「${view.name}」的外部配置已重新同步。`
    if (sync.state === 'not_required') return `「${view.name}」这次不需要同步外部配置${sync.reason ? `：${SYNC_REASONS[sync.reason]}` : ''}。`
    return `重试已执行，同步${sync.state === 'failed' ? '仍然失败' : '结果未知'}${sync.reason ? `：${SYNC_REASONS[sync.reason]}` : ''}。`
  })
}
async function check(view: ModuleView) {
  await run(view, 'check', async item => {
    const health = await actions.checkHealth(item)
    const label = { ok: '连通正常', degraded: '部分异常', down: '连接失败', unknown: '结果未知', not_configured: '尚未配置', not_applicable: '没有需要检测的外部服务' }[health.state]
    return `「${view.name}」检测完成：${label}。`
  })
}

// ---- 自定义页面入口 ----
const pageTarget = ref<CustomPage | 'new' | null>(null), pageRemoving = ref<CustomPage | null>(null)
function addPage() { if (canEditPages.value) { pageRemoving.value = null; pageTarget.value = 'new' } }
function editPage(page: CustomPage) { pageRemoving.value = null; pageTarget.value = page }
function removePage(page: CustomPage) { pageTarget.value = null; pageRemoving.value = page }
function pageDone(message: string) { pageTarget.value = null; pageRemoving.value = null; error.value = ''; notice.value = message }
function closePageEditor() { pageTarget.value = null; pageRemoving.value = null }

const refreshing = computed(() => management.isFetching.value)
function refresh() { error.value = ''; void actions.refresh(); void navigation.refresh() }
function guide() { update({ tab: 'guide', module: undefined }); void nextTick(() => document.getElementById('apc-deploy')?.scrollIntoView({ block: 'start' })) }

const tablist = ref<HTMLElement | null>(null)
async function tabKey(event: KeyboardEvent) {
  const index = TAB_KEYS.indexOf(tab.value)
  const next = event.key === 'ArrowRight' ? index + 1 : event.key === 'ArrowLeft' ? index - 1 : event.key === 'Home' ? 0 : event.key === 'End' ? TAB_KEYS.length - 1 : -2
  if (next === -2) return
  event.preventDefault()
  const target = (next + TAB_KEYS.length) % TAB_KEYS.length
  setTab(TAB_KEYS[target]!)
  await nextTick()
  tablist.value?.querySelectorAll<HTMLElement>('[role="tab"]')[target]?.focus()
}
const tabName: Record<Tab, string> = { modules: '功能模块', pages: '自定义页面', activity: '操作记录', guide: '接入指南' }
</script>

<template>
  <ManagementPage class="apc-page" title="应用中心" description="统一管理功能应用、页面入口与扩展能力。">
    <template #context><span class="mg-tag"><Layers aria-hidden="true" />当前工作空间：{{ auth.currentWorkspace?.name }}</span></template>
    <template #actions>
      <button type="button" class="apc-link-button" @click="setTab('guide')"><BookOpen aria-hidden="true" />应用接入指南</button>
      <button type="button" class="secondary-button" :disabled="refreshing" @click="refresh"><RefreshCw aria-hidden="true" />{{ refreshing ? '正在刷新…' : '刷新状态' }}</button>
      <button v-if="canEditPages" type="button" class="primary-button" @click="addPage"><Plus aria-hidden="true" />添加入口</button>
    </template>

    <div class="asv-layout apc-layout" :class="{ 'has-side': !!selected && wide }">
      <div class="asv-main apc-main">
        <section class="ag-summary" aria-label="应用统计">
          <dl class="ag-tiles">
            <div class="ag-tile tone-violet"><dt><LayoutGrid aria-hidden="true" />已接入模块</dt><dd>{{ value(data?.summary.registered) }}</dd></div>
            <div class="ag-tile tone-green"><dt><CircleCheck aria-hidden="true" />已启用</dt><dd>{{ value(data?.summary.enabled) }}</dd></div>
            <div class="ag-tile tone-amber"><dt><TriangleAlert aria-hidden="true" />需处理</dt><dd>{{ value(data?.summary.attention) }}</dd></div>
            <div class="ag-tile tone-violet"><dt><Layers aria-hidden="true" />自定义页面</dt><dd>{{ navigation.layout ? pages.length : '—' }}</dd></div>
          </dl>
          <p class="form-help">模块数来自已注册的功能模块，不含核心功能与自定义页面；需处理：同步失败 / 结果未知，或已启用但未部署、外部服务异常。</p>
        </section>

        <ActionFeedback :error="disabling ? '' : error" :notice="notice" />

        <div ref="tablist" class="sk-tabs apc-tabs" role="tablist" aria-label="应用中心">
          <button v-for="key in TAB_KEYS" :key="key" type="button" role="tab" :aria-selected="tab === key" :tabindex="tab === key ? 0 : -1" @click="setTab(key)" @keydown="tabKey">
            {{ tabName[key] }}<span v-if="key === 'modules' && data" class="apc-count">{{ data.summary.registered }}</span><span v-if="key === 'pages' && navigation.layout" class="apc-count">{{ pages.length }}</span>
          </button>
        </div>

        <div v-if="tab === 'modules'" role="tabpanel" aria-label="功能模块" class="apc-stack">
          <div class="ws-toolbar apc-toolbar">
            <label class="ws-search"><Search aria-hidden="true" /><span class="sr-only">搜索应用名称或功能</span><input v-model="search" type="search" placeholder="搜索应用名称或功能" maxlength="100" /></label>
            <select v-model="status" aria-label="按状态筛选"><option v-for="(label, key) in STATUS_FILTERS" :key="key" :value="key">{{ label }}</option></select>
          </div>
          <p v-if="management.isPending.value" role="status" class="ws-state-line">正在加载模块…</p>
          <div v-else-if="management.error.value && !data" class="ws-panel ws-state-line"><p role="alert">模块读取失败：{{ errorMessage(management.error.value) }}</p><button type="button" class="secondary-button" @click="management.refetch()">重试</button></div>
          <template v-else>
            <p v-if="management.error.value" class="form-help" role="status">刷新失败，显示的是上次取回的状态。</p>
            <p v-if="!vm.modules.length" class="ws-panel ws-state-line">还没有已注册的功能模块。</p>
            <p v-else-if="!shown.length" class="ws-panel ws-state-line">没有符合条件的模块。</p>
            <ModuleCard v-for="view in shown" :key="view.key" :view="view" :selected="selected === view.key" :busy="busyFor(view.key) === 'toggle'" @toggle="requestToggle(view)" @details="opener => openDetails(view, opener)" />
            <p v-if="vm.frontendOnly.length" class="form-help">前端还注册了 {{ vm.frontendOnly.join('、') }}，但后端没有：前后端版本不一致，请更新到同一版本。</p>
          </template>
          <CustomPagesPanel compact :layout="navigation.layout" :loaded="navigation.loaded" :error="navigation.error" :can-edit="canEditPages" @edit="editPage" @all="setTab('pages')" />
          <section class="apc-next" aria-label="接入下一款应用">
            <Puzzle aria-hidden="true" />
            <div><strong>接入下一款应用</strong><p class="form-help">按一套规范接入，新功能会出现在这里。</p></div>
            <button type="button" class="secondary-button" @click="setTab('guide')"><BookOpen aria-hidden="true" />查看接入指南</button>
          </section>
        </div>

        <div v-else-if="tab === 'pages'" role="tabpanel" aria-label="自定义页面" class="apc-stack">
          <CustomPagesPanel :layout="navigation.layout" :loaded="navigation.loaded" :error="navigation.error" :can-edit="canEditPages" @add="addPage" @edit="editPage" @remove="removePage" />
        </div>

        <div v-else-if="tab === 'activity'" role="tabpanel" aria-label="操作记录" class="apc-stack">
          <section class="ws-panel">
            <div class="mg-section-head"><div class="mg-section-title"><h2>操作记录</h2><p class="form-help">所有模块的启用、停用、外部配置同步与重试（含已移除模块的历史）。</p></div></div>
            <ModuleActivity :module-key="null" :names="names" label="所有模块的操作记录" />
          </section>
        </div>

        <div v-else role="tabpanel" aria-label="接入指南" class="apc-stack">
          <ModuleGuide :modules="vm.modules" :can-edit="canEditPages" @add="addPage" @pages="setTab('pages')" />
        </div>
      </div>

      <aside v-if="selected && wide" class="ws-panel asv-side apc-side" aria-label="应用详情" @keydown.esc="closeDetails">
        <ModuleDetail v-if="current" :view="current" aside :busy="busyFor(current.key)" :initial-tab="detailTab" :names="names" @close="closeDetails" @toggle="requestToggle(current)" @retry="retry(current)" @check="check(current)" @guide="guide" />
        <div v-else class="apc-detail">
          <div class="asv-side-head"><h2>应用详情</h2><button type="button" class="icon-button" aria-label="关闭：应用详情" @click="closeDetails">关闭</button></div>
          <p v-if="management.isPending.value" role="status" class="ws-state-line">正在加载…</p>
          <p v-else role="alert" class="ws-state-line">没有这个模块：它可能已经从工作台移除，历史记录可以在操作记录或审计日志里查看。</p>
        </div>
      </aside>
    </div>
    <DrawerDetail v-if="selected && !wide" title="应用详情" @close="closeDetails">
      <ModuleDetail v-if="current" :view="current" :busy="busyFor(current.key)" :initial-tab="detailTab" :names="names" @close="closeDetails" @toggle="requestToggle(current)" @retry="retry(current)" @check="check(current)" @guide="guide" />
      <p v-else-if="management.isPending.value" role="status" class="ws-state-line">正在加载…</p>
      <p v-else role="alert" class="ws-state-line">没有这个模块：它可能已经从工作台移除，历史记录可以在操作记录或审计日志里查看。</p>
    </DrawerDetail>

    <ModalPanel v-if="disabling" title="停用应用" :busy="!!busy" @close="disabling = null">
      <div class="apc-dialog">
        <p>停用「{{ disabling.name }}」？停用后入口、首页组件与智能体工具都会隐藏，后台任务不再处理这个工作空间。</p>
        <p><strong>数据与配置保留，再启用就恢复。</strong></p>
        <p v-if="disabling.item.capabilities.dependents.some(relation => relation.enabled)" class="form-help">有已启用的模块依赖它，需要先停用：{{ disabling.item.capabilities.dependents.filter(relation => relation.enabled).map(relation => relation.name).join('、') }}。</p>
        <div class="form-actions"><button type="button" class="primary-button danger-button" :disabled="!!busy" @click="toggle(disabling, false)">停用应用</button><button type="button" class="secondary-button" :disabled="!!busy" @click="disabling = null">取消</button></div>
      </div>
    </ModalPanel>
    <PageEntryEditor v-if="pageTarget || pageRemoving" :target="pageTarget" :removing="pageRemoving" @close="closePageEditor" @done="pageDone" />
  </ManagementPage>
</template>
