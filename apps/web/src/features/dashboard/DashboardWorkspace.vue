<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useDashboard } from './useDashboard'
import { exportDashboard, type WidgetValue } from '../../widgets/registry'
import type { Schema } from '../../services/api'
import DesktopGrid from '../../widgets/DesktopGrid.vue'
import WidgetFrame from '../../widgets/WidgetFrame.vue'
import LayoutFields from '../../widgets/LayoutFields.vue'
import WidgetEditor from './WidgetEditor.vue'
import DataState from '../../components/DataState.vue'
import ModalPanel from '../../components/ModalPanel.vue'
import { Ellipsis, LayoutDashboard, RefreshCw } from '@lucide/vue'
import { useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
// 首页页头（T-0164，首页效果图）：标题、问候语、更新时间、刷新、自定义首页（进入编辑模式）
defineProps<{ heading: string; greeting: string }>()
const model = reactive(useDashboard())
const auth = useAuthStore(), cache = useQueryClient()
const updated = computed(() => (model.updatedAt ? new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(model.updatedAt) : ''))
// 刷新：首页聚合之外，各组件自己读的数据（任务进展、我的待办、日程、早报）也一起重新读取
async function refreshAll() {
  const scope = auth.workspaceId
  // 提醒与日程（calendar … 'widget'）已由 reload 刷新，这里不再重复读取
  await Promise.all([model.reload(), cache.invalidateQueries({ predicate: (q) => ['task-progress', 'tasks', 'calendar', 'news', 'projects'].includes(String(q.queryKey[0])) && q.queryKey[2] === scope && q.queryKey[3] !== 'widget' })])
}
function addWidget() {
  if (model.dirty && !window.confirm('添加组件将放弃未保存的布局，继续吗？')) return
  model.failure = null; editor.value = 'new'
}
const mobile = ref(window.matchMedia?.('(max-width: 760px)').matches ?? false)
const media = window.matchMedia?.('(max-width: 760px)')
const setMobile = () => { mobile.value = media?.matches ?? false }
onMounted(() => media?.addEventListener('change', setMobile))
onUnmounted(() => media?.removeEventListener('change', setMobile))
const mode = ref<'import' | 'export' | null>(null)
const json = ref('')
const toolError = ref('')
const editor = ref<Schema['WidgetOut'] | 'new' | null>(null)
const bottom = computed(() => Math.max(0, ...model.widgets.map((w) => w.layout.desktop.y + w.layout.desktop.h)))
const mobileWidgets = computed(() => [...model.shownWidgets].sort((a, b) => a.layout.mobile.y - b.layout.mobile.y || a.id.localeCompare(b.id)))
function openTools(value: 'import' | 'export') {
  toolError.value = ''; json.value = ''
  if (value === 'export') {
    try { json.value = exportDashboard(model.dashboard!.title, model.widgets) }
    catch { toolError.value = '无法导出包含未知类型或无效配置的仪表盘。'; return }
  }
  if (!model.blocked) model.failure = null
  mode.value = value
}
function download() {
  const url = URL.createObjectURL(new Blob([json.value], { type: 'application/json' }))
  const link = document.createElement('a'); link.href = url; link.download = 'dashboard.json'; link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
async function importJson() { if (await model.importJson(json.value)) mode.value = null }
function configure(widget: Schema['WidgetOut']) {
  if (model.pending || model.blocked || (model.dirty && !window.confirm('配置组件将放弃未保存的布局，继续吗？'))) return
  editor.value = widget
}
async function saveWidget(value: WidgetValue) { if (await model.saveWidget(value, editor.value === 'new' ? undefined : editor.value ?? undefined)) editor.value = null }
// 不常用的操作收进“更多”菜单（T-0085）。reka-ui 关闭菜单后会把焦点还给触发按钮，会抢走对话框里的焦点：
// 选中一项时先阻止它，再在下一轮打开对话框（与顶栏“创建”菜单同样处理）
let chosen: (() => void) | null = null
function afterMenuClose(event: Event) {
  if (!chosen) return
  const action = chosen
  chosen = null
  event.preventDefault()
  setTimeout(action, 0)
}
function closeEditor() { if (!model.pending) { editor.value = null; if (!model.blocked) model.failure = null } }
</script>
<template>
  <section aria-label="仪表盘">
    <header id="dashboard-tools" class="home-header">
      <div><h1>{{ heading }}</h1><p>{{ greeting }}</p></div>
      <div class="home-actions">
        <span v-if="updated && !model.editing" class="home-updated">更新于 {{ updated }}</span>
        <button v-if="!model.editing" type="button" class="secondary-button" aria-label="刷新首页" title="刷新首页" :disabled="model.pending || model.fetching" @click="refreshAll"><RefreshCw aria-hidden="true" />刷新</button>
        <template v-if="model.dashboard && !model.loading && !model.denied">
          <button v-if="model.canEdit && !model.editing" type="button" class="secondary-button" :disabled="model.pending || model.fetching || model.blocked || !!model.queryError" @click="model.beginEdit"><LayoutDashboard aria-hidden="true" />自定义首页</button>
          <template v-if="model.editing">
            <span class="home-updated">{{ model.dashboard.title }} · 版本 {{ model.dashboard.version }}</span>
            <button class="secondary-button" :disabled="model.pending || model.blocked || model.widgets.length >= 50" @click="addWidget">添加组件</button>
            <button class="primary-button" :disabled="model.pending || model.blocked || !model.dirty" @click="model.saveLayout">{{ model.pending ? '正在保存…' : '保存布局' }}</button>
            <button class="secondary-button" :disabled="model.pending" @click="model.editing = false; model.dirty = false">取消编辑</button>
          </template>
          <select v-if="model.choices.length" aria-label="选择仪表盘" :value="model.dashboard.id" :disabled="model.pending || model.blocked" @change="model.choose(($event.target as HTMLSelectElement).value)"><option v-for="choice in model.choices" :key="choice.id" :value="choice.id">{{ choice.title }}{{ choice.is_default ? ' · 首页' : '' }}</option></select>
          <DropdownMenu>
            <DropdownMenuTrigger class="shell-icon" aria-label="更多仪表盘操作" title="更多"><Ellipsis aria-hidden="true" /></DropdownMenuTrigger>
            <DropdownMenuContent align="end" :collision-padding="8" class="w-48" @close-auto-focus="afterMenuClose">
              <DropdownMenuItem :disabled="model.pending || model.dirty" @select="chosen = () => openTools('export')">导出 JSON</DropdownMenuItem>
              <DropdownMenuItem v-if="model.canEdit" :disabled="model.pending || model.dirty || model.blocked" @select="chosen = () => openTools('import')">导入 JSON</DropdownMenuItem>
              <DropdownMenuItem :disabled="model.pending || model.dirty || model.blocked" @select="model.loadChoices">选择仪表盘</DropdownMenuItem>
              <DropdownMenuItem v-if="model.canEdit && !model.dashboard.is_default" :disabled="model.pending || model.dirty || model.blocked" @select="model.makeDefault">设为首页</DropdownMenuItem>
              <DropdownMenuItem v-if="model.canEdit" :disabled="model.pending || model.blocked" @select="chosen = () => model.resetDashboard()">恢复默认首页</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </template>
      </div>
    </header>
    <p v-if="model.error && !mode && !editor" role="alert" class="feedback">{{ model.error }}</p>
    <p v-if="model.notice" role="status" class="feedback">{{ model.notice }}</p>
    <button v-if="model.blocked" class="secondary-button" :disabled="model.pending" @click="model.reload">重新加载最新版本</button>
    <p v-if="toolError" role="alert">{{ toolError }}</p>
    <p v-if="model.editing" class="form-help">{{ model.dirty ? '布局有未保存的修改。' : '拖动卡片标题，或拖动右下角调整大小。' }}也可展开“键盘调整布局”直接输入。</p>
    <DataState :loading="model.loading" :error="model.queryError" :empty="!model.widgets.length" empty-text="暂无组件，可以添加组件或导入布局。" :stale="!!model.overview && !model.denied" @retry="model.reload" />
    <template v-if="!model.loading && !model.denied">
      <div v-if="mobile" class="mobile-widget-grid">
        <WidgetFrame v-for="item in mobileWidgets" :key="item.id" :widget="item" :overview="model.overview" :editing="model.editing && !model.pending && !model.blocked" :failed="!!model.queryError" :loading="model.cardLoading" :style="{ minHeight: `${item.layout.mobile.h * 64}px` }" @retry="model.reload" @configure="configure(item)" @remove="model.removeWidget(item)"><LayoutFields v-if="model.editing" :widget="item" :disabled="model.pending || model.blocked" @change="model.changeLayout([$event])" /></WidgetFrame>
      </div>
      <DesktopGrid v-else :key="model.dashboard?.id + model.shownWidgets.map(w => w.id).join()" :widgets="model.shownWidgets" :editing="model.editing && !model.pending && !model.blocked" @change="model.changeLayout">
        <template #default="{ widget: item }"><WidgetFrame :widget="item" :overview="model.overview" :editing="model.editing && !model.pending && !model.blocked" :failed="!!model.queryError" :loading="model.cardLoading" @retry="model.reload" @configure="configure(item)" @remove="model.removeWidget(item)"><LayoutFields v-if="model.editing" :widget="item" :disabled="model.pending || model.blocked" @change="model.changeLayout([$event])" /></WidgetFrame></template>
      </DesktopGrid>
    </template>
    <WidgetEditor v-if="editor" :widget="editor === 'new' ? undefined : editor" :bottom="bottom" :busy="model.pending" :error="model.error" @close="closeEditor" @save="saveWidget" />
    <ModalPanel v-if="mode" :title="mode === 'import' ? '导入布局' : '导出布局'" :busy="model.pending" @close="!model.pending && (mode = null)"><p class="form-help">{{ mode === 'import' ? '导入将创建新仪表盘，不覆盖当前首页。' : '仅导出标题、配置与布局，不包含对象 ID、版本或时间。' }}</p><label class="form-field">布局 JSON<textarea v-model="json" rows="12" :readonly="mode === 'export'" :disabled="model.pending" spellcheck="false"></textarea></label><p v-if="model.error" role="alert">{{ model.error }}</p><button v-if="mode === 'import'" class="primary-button" :disabled="model.pending || model.blocked" @click="importJson">{{ model.pending ? '正在创建…' : '创建新仪表盘' }}</button><button v-else class="primary-button" @click="download">下载 JSON</button></ModalPanel>
  </section>
</template>
