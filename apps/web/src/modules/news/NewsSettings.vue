<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { CircleAlert, CircleCheck, ExternalLink, FileText, Pencil, RefreshCw, TriangleAlert } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { ApiError, isModuleDisabled, type Schema } from '../../services/api'
import ManagementPage from '../../components/management/ManagementPage.vue'
import NewsScheduleSettings from './NewsScheduleSettings.vue'
import NewsSettingsSummary from './NewsSettingsSummary.vue'
import NewsSourceSettings from './NewsSourceSettings.vue'
import NewsTopicSettings from './NewsTopicSettings.vue'
import NewsModelSettings from './NewsModelSettings.vue'
import NewsChannelSettings from './NewsChannelSettings.vue'
import NewsLearnedSettings from './NewsLearnedSettings.vue'
import NewsSettingsStatus from './NewsSettingsStatus.vue'
import { PLATFORMS, newsError, useNewsActions, useNewsAgents, useNewsSettings, useNewsStatus, type ChannelId, type NewsSettings } from './api'
import { buildBody, checkForm, fieldErrors, sameForm, toForm, type FormPatch, type NewsForm } from './settingsForm'
import { SETTINGS_TABS, availability, type Feedback, type SettingsTab } from './settingsState'
import './news.css'

// 设置 → 新闻早报（T-0153；T-0195 按效果图 06 改版，方案 docs/v2/ai-admin-redesign 计划 B5）：owner 改，admin 只读。
// - 五个标签编辑同一份草稿（draft）；子组件只发出修改，保存只在这里，一次提交整份设置并带上 version（乐观并发）。
// - 草稿与最近保存的设置（saved）分开：没有改动时，别处的修改（SSE、刷新）直接显示；有改动时不覆盖，只提示，
//   保存遇到版本冲突时保留草稿，由用户决定“重新加载”。
// - “刷新状态”只重新读取（GET）；测试推送与现在生成是 owner 明确点的动作，有未保存更改时先保存，
//   保存失败或配置文件没写成功就不执行，免得用旧的推送设置。
// - 密钥只写不读（只显示“已设置”）；保存后工作台重新生成 TrendRadar 的配置文件，下一次运行生效。
const auth = useAuthStore(), scope = auth.workspaceId!
const route = useRoute(), router = useRouter()
const settings = useNewsSettings(scope), status = useNewsStatus(scope), agents = useNewsAgents(scope), actions = useNewsActions(scope)
const canEdit = computed(() => auth.isOwner)

const draft = ref<NewsForm | null>(null), saved = ref<NewsSettings | null>(null)
const busy = ref<'' | 'save' | 'test' | 'generate'>('')
const saveFeedback = ref<Feedback | null>(null), conflict = ref(false)
const actionFeedback = ref<Feedback | null>(null), pushResults = ref<Schema['NewsPushResult'][] | null>(null)
const openFeed = ref<number | null>(null), refreshing = ref(false)

const current = computed(() => settings.data.value ?? null)
const baseline = computed(() => (saved.value ? toForm(saved.value) : null))
const dirty = computed(() => !!draft.value && !!baseline.value && !sameForm(draft.value, baseline.value))
// 有未保存更改时，服务器上的设置已经换了版本（别人改了、或别处顺手加了排除词）
const outdated = computed(() => !!current.value && !!saved.value && current.value.version !== saved.value.version)

function load(value: NewsSettings) { draft.value = toForm(value); saved.value = value; conflict.value = false }
watch(current, (value) => {
  if (value && (!draft.value || (!dirty.value && value.version !== saved.value?.version))) load(value)
}, { immediate: true })

function patch(change: FormPatch) {
  if (!draft.value || !canEdit.value) return
  draft.value = { ...draft.value, ...change }
  if (saveFeedback.value?.tone === 'good') saveFeedback.value = null
}

const choices = computed(() => (current.value?.platform_choices.length ? current.value.platform_choices : PLATFORMS))
const instances = computed(() => agents.data.value)
const instancesFailed = computed(() => !agents.data.value && !!agents.error.value)
const statusFailed = computed(() => !status.data.value && !!status.error.value)
const state = computed(() => (current.value ? availability(current.value, status.data.value, statusFailed.value) : null))
const ICONS = { green: CircleCheck, red: CircleAlert, amber: TriangleAlert, muted: CircleAlert, blue: CircleCheck } as const

const tab = computed<SettingsTab>(() => (SETTINGS_TABS.some(t => t.key === route.query.tab) ? route.query.tab as SettingsTab : 'basic'))
const tabName = computed(() => SETTINGS_TABS.find(t => t.key === tab.value)!.name)
function go(next: SettingsTab) { void router.replace({ query: { ...route.query, tab: next === 'basic' ? undefined : next } }) }
function editFeed(index: number) { openFeed.value = index; go('sources') }
// 标签条的键盘操作：左右方向键、Home / End 切换并移动焦点
function onTabKey(event: KeyboardEvent) {
  const index = SETTINGS_TABS.findIndex(t => t.key === tab.value)
  const next = { ArrowRight: index + 1, ArrowLeft: index - 1, Home: 0, End: SETTINGS_TABS.length - 1 }[event.key]
  if (next === undefined) return
  event.preventDefault()
  const target = (next + SETTINGS_TABS.length) % SETTINGS_TABS.length
  go(SETTINGS_TABS[target]!.key)
  ;(event.currentTarget as HTMLElement).querySelectorAll<HTMLElement>('[role="tab"]')[target]?.focus()
}

const secretsSet = (channel: ChannelId) => saved.value?.channels[channel]?.secrets_set ?? []
async function save(): Promise<NewsSettings | null> {
  const form = draft.value, base = saved.value
  if (!form || !base || busy.value || !canEdit.value) return null
  saveFeedback.value = null; conflict.value = false
  const found = checkForm(form, secretsSet)
  if (found.length) { saveFeedback.value = { tone: 'bad', text: '还有几处要改：', problems: found }; return null }
  busy.value = 'save'
  try {
    const result = await actions.saveSettings(buildBody(form, base.version))
    load(result)
    saveFeedback.value = result.render_error
      ? { tone: 'bad', text: '设置已保存，但配置文件没有写成功：TrendRadar 仍按上一次写成功的配置运行。原因见右侧“配置文件”，解决后重新保存。' }
      : { tone: 'good', text: '设置已保存：TrendRadar 下一次运行时生效。' }
    return result
  } catch (cause) {
    conflict.value = cause instanceof ApiError && cause.code === 'VERSION_CONFLICT'
    const problems = fieldErrors(cause)
    saveFeedback.value = { tone: 'bad', text: problems.length ? '有几处不符合要求：' : newsError(cause), problems }
    // 版本冲突：读一下最新的设置（只读），草稿保留
    if (conflict.value) void settings.refetch()
    return null
  } finally { busy.value = '' }
}
function cancel() {
  if (current.value) load(current.value)
  saveFeedback.value = null; openFeed.value = null
}
async function reload() {
  saveFeedback.value = null
  const result = await settings.refetch()
  if (result.data) { load(result.data); openFeed.value = null; saveFeedback.value = { tone: 'good', text: '已重新加载最新的设置。' } }
  else if (result.error) saveFeedback.value = { tone: 'bad', text: newsError(result.error) }
}
async function refreshStatus() {
  if (refreshing.value) return
  refreshing.value = true
  try { await Promise.all([status.refetch(), settings.refetch(), agents.refetch()]) } finally { refreshing.value = false }
}

// 测试推送与现在生成只用已保存的设置：有未保存更改时先保存；保存失败或配置文件没写成功就不执行
async function savedFirst(): Promise<boolean> {
  if (!dirty.value) return true
  const result = await save()
  if (!result) { actionFeedback.value = { tone: 'bad', text: '没有执行：设置还没有保存成功（见页面底部的提示）。测试推送和现在生成只用已保存的设置。' }; return false }
  if (result.render_error) { actionFeedback.value = { tone: 'bad', text: '没有执行：设置已保存，但配置文件没有写成功，TrendRadar 还在用旧配置。解决后再试。' }; return false }
  return true
}
async function testPush() {
  if (!canEdit.value || busy.value) return
  actionFeedback.value = null; pushResults.value = null
  if (!(await savedFirst())) return
  busy.value = 'test'
  try {
    const results = await actions.testPush()
    pushResults.value = results
    const ok = results.filter(r => r.ok).length
    actionFeedback.value = results.length
      ? { tone: ok === results.length ? 'good' : 'warn', text: `TrendRadar 报告：${ok} 个渠道发送成功、${results.length - ok} 个失败。“成功”只表示已经发出，有没有收到请到各渠道里看。` }
      : { tone: 'warn', text: 'TrendRadar 没有返回任何渠道的结果，请到各渠道里看有没有收到。' }
  } catch (cause) { actionFeedback.value = { tone: 'bad', text: newsError(cause) } }
  finally { busy.value = '' }
}
async function generate() {
  if (!canEdit.value || busy.value) return
  actionFeedback.value = null
  if (!(await savedFirst())) return
  busy.value = 'generate'
  try {
    await actions.generate()
    actionFeedback.value = { tone: 'good', text: '已让智能体开始写早报：写好后在“早报”页查看，并按推送渠道推送。' }
  } catch (cause) { actionFeedback.value = { tone: 'bad', text: newsError(cause) } }
  finally { busy.value = '' }
}
</script>

<template>
  <ManagementPage class="nws-page" title="新闻早报" description="配置早报计划、新闻来源、兴趣主题、大模型与推送渠道；owner 修改，admin 只读。" aside-label="早报运行状态与推送渠道">
    <template #actions>
      <RouterLink class="secondary-button" to="/news"><ExternalLink aria-hidden="true" />前往早报</RouterLink>
      <button type="button" class="secondary-button" :aria-busy="refreshing" :disabled="refreshing" @click="refreshStatus"><RefreshCw aria-hidden="true" />刷新状态</button>
    </template>

    <p v-if="settings.isPending.value" role="status" class="ws-state-line">正在加载…</p>
    <div v-else-if="!current" class="ws-panel mg-section">
      <p v-if="isModuleDisabled(settings.error.value)" role="status">功能已停用：新闻早报在当前工作空间没有启用。</p>
      <template v-else>
        <p role="alert">{{ newsError(settings.error.value) }}</p>
        <button type="button" class="secondary-button" @click="settings.refetch()">重试</button>
      </template>
    </div>
    <template v-else-if="draft && state">
      <div class="nws-bar" :class="`tone-${state.tone}`" role="region" aria-label="早报状态摘要">
        <span class="nws-bar-item nws-bar-state"><component :is="ICONS[state.tone]" aria-hidden="true" /><strong>{{ state.label }}</strong></span>
        <span class="nws-bar-item"><FileText aria-hidden="true" />{{ current.config_rev ? `最近写入配置 #${current.config_rev}` : '还没有写入配置文件' }}</span>
        <span v-if="current.render_error" class="nws-bar-item is-bad"><CircleAlert aria-hidden="true" />配置文件写入失败</span>
        <span v-if="dirty" class="nws-bar-item nws-bar-dirty"><Pencil aria-hidden="true" />当前表单有未保存更改</span>
      </div>

      <p v-if="!canEdit" role="note" class="nws-note">只有 owner 能修改新闻早报的设置；你看到的是只读视图。</p>
      <p v-if="!current.deployed" role="note" class="nws-note tone-amber">这个部署没有安装 TrendRadar：设置可以保存，但不会抓取新闻，也不会推送。安装方法见部署指南的“新闻早报”一节。</p>
      <p v-if="current.taken_by_other" role="note" class="nws-note tone-red">另一个工作空间已经启用了早报（一个部署只能有一个 TrendRadar），这里启用不了；保存设置不会抢占它。</p>
      <p v-if="outdated" role="status" class="nws-note tone-amber">设置刚被别处修改了（例如有人顺手加了排除词）。这里没保存的更改还在，但直接保存会因版本冲突失败：点页面底部的“重新加载”拿到最新的设置（会丢掉这里的更改）。</p>

      <div class="nws-tabs" role="tablist" aria-label="新闻早报设置" @keydown="onTabKey">
        <button v-for="t in SETTINGS_TABS" :key="t.key" type="button" role="tab" :aria-selected="tab === t.key" :tabindex="tab === t.key ? 0 : -1" @click="go(t.key)">{{ t.name }}</button>
      </div>
      <div class="nws-panel" role="tabpanel" :aria-label="tabName">
        <template v-if="tab === 'basic'">
          <NewsScheduleSettings :form="draft" :timezone="current.timezone" :can-edit="canEdit" :disabled="!canEdit || busy === 'save'" :instances="instances" :instances-failed="instancesFailed" @patch="patch" />
          <NewsSettingsSummary :form="draft" :choices="choices" :can-edit="canEdit" @tab="go" @feed="editFeed" />
        </template>
        <NewsSourceSettings v-else-if="tab === 'sources'" v-model:open="openFeed" :form="draft" :choices="choices" :can-edit="canEdit" :disabled="!canEdit || busy === 'save'" @patch="patch" />
        <NewsTopicSettings v-else-if="tab === 'topics'" :form="draft" :can-edit="canEdit" :disabled="!canEdit || busy === 'save'" @patch="patch" />
        <template v-else-if="tab === 'model'">
          <NewsModelSettings :form="draft" :key-set="saved?.ai_api_key_set ?? false" :disabled="!canEdit || busy === 'save'" @patch="patch" />
          <NewsChannelSettings :form="draft" :saved="saved ?? current" :disabled="!canEdit || busy === 'save'" @patch="patch" />
        </template>
        <NewsLearnedSettings v-else :learned="current.learned" @tab="go" />
      </div>

      <div v-if="canEdit && (dirty || saveFeedback)" class="nws-savebar" :class="{ 'is-dirty': dirty }" role="region" aria-label="保存更改">
        <div class="nws-savebar-main">
          <p v-if="dirty" class="nws-savebar-state"><Pencil aria-hidden="true" />有未保存更改 · 保存后 TrendRadar 下一次运行生效</p>
          <div v-if="saveFeedback" :role="saveFeedback.tone === 'good' ? 'status' : 'alert'" class="nws-feedback" :class="`tone-${saveFeedback.tone}`">
            <p>{{ saveFeedback.text }}</p>
            <ul v-if="saveFeedback.problems?.length"><li v-for="p in saveFeedback.problems" :key="p">{{ p }}</li></ul>
            <p v-if="conflict" class="form-help">重新加载会丢掉这里没保存的修改。</p>
          </div>
        </div>
        <div class="nws-savebar-actions">
          <button v-if="conflict || outdated" type="button" class="secondary-button" :disabled="!!busy" @click="reload">重新加载</button>
          <template v-if="dirty">
            <button type="button" class="secondary-button" :disabled="!!busy" @click="cancel">取消更改</button>
            <button type="button" class="primary-button" :disabled="!!busy" @click="save">{{ busy === 'save' ? '正在保存…' : '保存设置' }}</button>
          </template>
          <button v-else type="button" class="secondary-button" @click="saveFeedback = null">知道了</button>
        </div>
      </div>
    </template>

    <template v-if="current" #aside>
      <NewsSettingsStatus :settings="current" :status="status.data.value" :status-failed="statusFailed" :instances="instances" :instances-failed="instancesFailed" :can-edit="canEdit" :dirty="dirty" :busy="busy" :can-generate="draft?.briefing_mode === 'agent'" :results="pushResults" :feedback="actionFeedback" @test="testPush" @generate="generate" @channels="go('model')" />
    </template>
  </ManagementPage>
</template>
