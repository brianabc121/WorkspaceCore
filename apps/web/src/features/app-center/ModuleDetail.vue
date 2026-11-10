<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { ArrowRight, CircleCheck, ExternalLink, Info, Layers, Power, Radio, RefreshCw, Settings, TriangleAlert, X } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { canSee } from '../../app/navigation'
import { formatTime } from '../time'
import ModuleActivity from './ModuleActivity.vue'
import { HEALTH_LABELS, ROLE_LABELS, SERVICE_STATES, SYNC_REASONS, type ModuleView } from './viewModel'

// 应用详情（T-0211，效果图右侧；方案第 3.2 节）：概览 / 功能能力 / 操作记录。
// 概览把启用、部署、服务连通、外部配置同步四项分开；同步失败明确说明“开关已保存”。
// 检测连接、重试同步是显式操作，由页面执行（这里只发事件）；只有 owner 能启停与重试，admin 只读、可以检测。
export type DetailTab = 'overview' | 'capabilities' | 'activity'
const TABS: { key: DetailTab; name: string }[] = [{ key: 'overview', name: '概览' }, { key: 'capabilities', name: '功能能力' }, { key: 'activity', name: '操作记录' }]
const RISK: Record<string, string> = { read: '只读', write: '写入（需审批）', critical: '高风险（需审批）', knowledge: '知识库', direct: '直接执行' }
const REASONS: Record<string, string> = { not_checked: '还没检测过', not_deployed: '没有部署', timeout: '超时', unreachable: '连不上' }
const props = defineProps<{ view: ModuleView; aside?: boolean; busy: string | null; initialTab?: DetailTab; names: ReadonlyMap<string, string> }>()
const emit = defineEmits<{ close: []; toggle: []; retry: []; check: []; guide: [] }>()
const auth = useAuthStore()
const tab = ref<DetailTab>(props.initialTab ?? 'overview')
watch(() => props.initialTab, value => { if (value) tab.value = value })
watch(() => props.view.key, () => { tab.value = props.initialTab ?? 'overview' })
const tablist = ref<HTMLElement | null>(null)
async function key(event: KeyboardEvent) {
  const index = TABS.findIndex(t => t.key === tab.value)
  const next = event.key === 'ArrowRight' ? index + 1 : event.key === 'ArrowLeft' ? index - 1 : event.key === 'Home' ? 0 : event.key === 'End' ? TABS.length - 1 : -2
  if (next === -2) return
  event.preventDefault()
  tab.value = TABS[(next + TABS.length) % TABS.length]!.key
  await nextTick()
  tablist.value?.querySelector<HTMLElement>('[aria-selected="true"]')?.focus()
}

const item = computed(() => props.view.item)
const module = computed(() => item.value.module)
const capabilities = computed(() => item.value.capabilities)
const visible = (path: string) => {
  const nav = props.view.nav.find(entry => entry.path === path)
  return !nav || canSee(nav, auth)
}
const primary = computed(() => (module.value.enabled && props.view.primary && visible(props.view.primary.path) ? props.view.primary : null))
const settings = computed(() => (module.value.enabled && props.view.settings && visible(props.view.settings.path) ? props.view.settings : null))
const serviceNames = computed(() => capabilities.value.services.map(service => service.name).join('、'))
const syncFailed = computed(() => item.value.sync.state === 'failed' || item.value.sync.state === 'unknown')
const healthTime = computed(() => (item.value.health.checked_at ? `最近检测 ${formatTime(item.value.health.checked_at)}${item.value.health.stale ? '（已过期）' : ''}` : ''))
const relationName = (relation: { key: string; name: string }) => relation.name || props.names.get(relation.key) || relation.key
const toolSummary = computed(() => {
  const tools = capabilities.value.tools
  if (!tools.length) return '无'
  return `${tools.length} 项 · ${props.view.counts.readOnlyTools ? '只读' : '含写入（需审批）'}`
})
const cronSummary = computed(() => (capabilities.value.crons.length ? [...new Set(capabilities.value.crons.map(cron => cron.schedule_summary))].join('、') : '无'))
const dependencySummary = computed(() => (capabilities.value.depends_on.length ? capabilities.value.depends_on.map(relationName).join('、') : '无'))
</script>

<template>
  <section class="apc-detail" :aria-label="`应用详情：${view.name}`">
    <div v-if="aside" class="asv-side-head">
      <h2>应用详情</h2>
      <button type="button" class="icon-button" aria-label="关闭：应用详情" @click="emit('close')"><X aria-hidden="true" /></button>
    </div>
    <div class="apc-detail-title">
      <span class="apc-app-icon" aria-hidden="true"><component :is="view.icon" /></span>
      <div><h3>{{ view.name }}</h3><p class="form-help">v{{ view.version }} · 功能模块<template v-if="view.category"> · {{ view.category }}</template></p></div>
    </div>
    <div ref="tablist" class="sk-tabs apc-detail-tabs" role="tablist" aria-label="应用详情">
      <button v-for="t in TABS" :id="`apc-tab-${t.key}`" :key="t.key" type="button" role="tab" :aria-selected="tab === t.key" :tabindex="tab === t.key ? 0 : -1" :aria-controls="`apc-panel-${t.key}`" @click="tab = t.key" @keydown="key">{{ t.name }}</button>
    </div>

    <div v-if="tab === 'overview'" id="apc-panel-overview" role="tabpanel" aria-labelledby="apc-tab-overview" class="apc-detail-body">
      <h4>运行与部署</h4>
      <ul class="apc-facts">
        <li><Power aria-hidden="true" /><span>工作空间开关</span><span class="asv-state" :class="`tone-${view.enabled.tone}`">{{ view.enabled.text }}</span></li>
        <li>
          <Layers aria-hidden="true" /><span>部署配置</span><span class="asv-state" :class="`tone-${view.deploy.tone}`">{{ view.deploy.text }}</span>
          <small v-if="module.missing.length" class="apc-fact-note">缺少部署设置：<code v-for="name in module.missing" :key="name">{{ name }}</code></small>
        </li>
        <li>
          <Radio aria-hidden="true" /><span>服务连通性</span><span class="asv-state" :class="`tone-${view.health.tone}`">{{ view.health.text }}</span>
          <small v-if="healthTime" class="apc-fact-note">{{ healthTime }}</small>
        </li>
        <li>
          <RefreshCw aria-hidden="true" /><span>外部配置同步</span><span class="asv-state" :class="`tone-${view.sync.tone}`">{{ view.sync.text }}</span>
          <small v-if="item.sync.reason" class="apc-fact-note">{{ SYNC_REASONS[item.sync.reason] }}<template v-if="item.sync.finished_at"> · {{ formatTime(item.sync.finished_at) }}</template></small>
        </li>
      </ul>
      <div v-if="item.health.checks.length" class="apc-checks">
        <p v-for="check in item.health.checks" :key="check.id" class="form-help">{{ check.name }}：{{ (SERVICE_STATES[check.status] ?? HEALTH_LABELS.unknown).text }}<template v-if="check.reason && check.status !== 'ok'">（{{ REASONS[check.reason] ?? check.reason }}）</template><template v-if="check.latency_ms !== null && check.latency_ms !== undefined && check.status === 'ok'"> · {{ check.latency_ms }} ms</template></p>
        <button v-if="item.actions.can_check_health" type="button" class="secondary-button apc-button" :disabled="!!busy" @click="emit('check')"><Radio aria-hidden="true" />{{ busy === 'check' ? '正在检测…' : '检测连接' }}</button>
      </div>

      <div v-if="!module.ready" class="apc-callout is-warning">
        <TriangleAlert aria-hidden="true" />
        <div><strong>{{ serviceNames || view.name }} 尚未配置</strong><p>先完成外部服务部署，再检测连接状态。没部署时也可以启用，页面会提示未部署。</p></div>
        <button type="button" class="secondary-button" @click="emit('guide')">查看部署指引</button>
      </div>
      <div v-if="syncFailed" class="apc-callout" :class="item.sync.state === 'failed' ? 'is-danger' : 'is-warning'" role="status">
        <TriangleAlert aria-hidden="true" />
        <div><strong>开关已保存，外部配置{{ item.sync.state === 'failed' ? '同步失败' : '同步结果未知' }}</strong><p>{{ item.sync.reason ? SYNC_REASONS[item.sync.reason] : '原因未知' }}。重试会按当前开关重新同步，不会改变开关。</p></div>
        <button v-if="item.actions.can_retry_sync" type="button" class="primary-button" :disabled="!!busy" @click="emit('retry')">{{ busy === 'retry' ? '正在重试…' : '重试同步' }}</button>
      </div>

      <h4>应用入口</h4>
      <div v-if="primary || settings" class="apc-entry-links">
        <RouterLink v-if="primary" :to="primary.path"><ExternalLink aria-hidden="true" />打开{{ primary.title }}页面</RouterLink>
        <RouterLink v-if="settings" :to="settings.path"><Settings aria-hidden="true" />{{ settings.title }}设置<ArrowRight aria-hidden="true" /></RouterLink>
      </div>
      <p v-else class="form-help">{{ view.frontendMissing ? '前后端版本不一致，入口暂不可用。' : module.enabled ? '这个模块没有提供主页面或设置页。' : '启用后可以打开应用与设置。' }}</p>

      <h4>声明能力</h4>
      <dl class="apc-declared">
        <div><dt>页面入口</dt><dd>{{ view.nav.length ? view.nav.map(entry => entry.title).join(' / ') : '无' }}</dd></div>
        <div><dt>首页组件</dt><dd>{{ view.widgetTitles.length ? view.widgetTitles.join('、') : '无' }}</dd></div>
        <div><dt>智能体工具</dt><dd>{{ toolSummary }}</dd></div>
        <div><dt>后台任务</dt><dd>{{ cronSummary }}</dd></div>
        <div><dt>模块依赖</dt><dd>{{ dependencySummary }}</dd></div>
      </dl>
      <p class="form-help apc-hint"><Info aria-hidden="true" />这些是模块清单里的声明；智能体能否调用工具仍看实例权限与审批策略，不会因为启用就授权给所有实例。</p>

      <h4>停用影响</h4>
      <div class="apc-impact">
        <p><Info aria-hidden="true" />入口、首页组件与智能体工具将停用；后台任务不再处理这个工作空间。</p>
        <p class="is-safe"><CircleCheck aria-hidden="true" />数据与配置保留，再启用可恢复。</p>
      </div>

      <div class="apc-detail-actions">
        <button v-if="item.actions.can_toggle" type="button" class="secondary-button" :disabled="!!busy" @click="emit('toggle')">{{ busy === 'toggle' ? '正在保存…' : module.enabled ? '停用应用' : '启用应用' }}</button>
        <RouterLink v-if="settings" class="primary-button" :to="settings.path">应用设置</RouterLink>
        <button type="button" class="apc-link-button" @click="tab = 'activity'">查看操作记录<ArrowRight aria-hidden="true" /></button>
      </div>
      <p v-if="!item.actions.can_toggle" class="form-help">你可以查看；只有 owner 能启用、停用或重试同步。</p>
    </div>

    <div v-else-if="tab === 'capabilities'" id="apc-panel-capabilities" role="tabpanel" aria-labelledby="apc-tab-capabilities" class="apc-detail-body">
      <h4>导航入口</h4>
      <ul v-if="view.nav.length" class="apc-list"><li v-for="entry in view.nav" :key="entry.path"><strong>{{ entry.title }}</strong><code>{{ entry.path }}</code><small>{{ ROLE_LABELS[entry.minRole ?? 'viewer'] }}可见</small></li></ul>
      <p v-else class="form-help">无</p>
      <h4>首页组件</h4>
      <p class="form-help">{{ view.widgetTitles.length ? view.widgetTitles.join('、') : '无' }}</p>
      <h4>智能体工具</h4>
      <ul v-if="capabilities.tools.length" class="apc-list"><li v-for="tool in capabilities.tools" :key="tool.name"><code>{{ tool.name }}</code><small>{{ RISK[tool.risk] ?? tool.risk }}</small></li></ul>
      <p v-else class="form-help">无</p>
      <h4>后台任务</h4>
      <ul v-if="capabilities.crons.length" class="apc-list"><li v-for="cron in capabilities.crons" :key="cron.id"><code>{{ cron.id }}</code><small>{{ cron.schedule_summary }}</small></li></ul>
      <p v-else class="form-help">无</p>
      <h4>依赖关系</h4>
      <p class="form-help">依赖：<template v-if="capabilities.depends_on.length"><template v-for="(relation, index) in capabilities.depends_on" :key="relation.key">{{ index ? '、' : '' }}{{ relationName(relation) }}（{{ relation.enabled ? '已启用' : '未启用' }}）</template></template><template v-else>无</template></p>
      <p class="form-help">被依赖：<template v-if="capabilities.dependents.length"><template v-for="(relation, index) in capabilities.dependents" :key="relation.key">{{ index ? '、' : '' }}{{ relationName(relation) }}（{{ relation.enabled ? '已启用，停用本模块前要先停用它' : '未启用' }}）</template></template><template v-else>无</template></p>
      <h4>外部服务与实时事件</h4>
      <p class="form-help">外部服务：{{ serviceNames || '无' }}<template v-if="capabilities.event_topics.length"> · 实时事件：{{ capabilities.event_topics.join('、') }}</template> · 启停后同步外部配置：{{ capabilities.has_sync_hook ? '是' : '否' }}</p>
      <h4>访问说明</h4>
      <p class="form-help">只有 owner 能启用、停用或重试同步，admin 只读、可以检测连接；页面入口按上面的角色显示；智能体工具沿用实例权限与审批策略。应用中心不能修改任何人的角色。</p>
    </div>

    <div v-else id="apc-panel-activity" role="tabpanel" aria-labelledby="apc-tab-activity" class="apc-detail-body">
      <ModuleActivity :module-key="view.key" :label="`${view.name}的操作记录`" />
    </div>
  </section>
</template>
