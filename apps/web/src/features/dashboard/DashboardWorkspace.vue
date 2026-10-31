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
const model = reactive(useDashboard())
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
function closeEditor() { if (!model.pending) { editor.value = null; if (!model.blocked) model.failure = null } }
</script>
<template>
  <section aria-label="仪表盘">
    <div id="dashboard-tools" class="dashboard-toolbar">
      <div><h2>{{ model.dashboard?.title ?? '首页' }}</h2><span v-if="model.dashboard" class="form-help">版本 {{ model.dashboard.version }}</span></div>
      <div class="form-actions">
        <button class="secondary-button" :disabled="model.pending || model.fetching" @click="model.reload">刷新首页</button>
        <template v-if="model.dashboard && !model.loading && !model.denied">
          <button v-if="model.canEdit && !model.editing" class="primary-button" :disabled="model.pending || model.blocked || !!model.queryError" @click="model.beginEdit">编辑布局</button>
          <template v-if="model.editing"><button class="primary-button" :disabled="model.pending || model.blocked || !model.dirty" @click="model.saveLayout">{{ model.pending ? '正在保存…' : '保存布局' }}</button><button class="secondary-button" :disabled="model.pending" @click="model.editing = false; model.dirty = false">取消编辑</button></template>
          <button v-if="model.canEdit && !model.editing" class="secondary-button" :disabled="model.pending || model.blocked || model.widgets.length >= 50" @click="model.failure = null; editor = 'new'">添加组件</button>
          <button class="secondary-button" :disabled="model.pending || model.dirty" @click="openTools('export')">导出 JSON</button>
          <button v-if="model.canEdit" class="secondary-button" :disabled="model.pending || model.dirty || model.blocked" @click="openTools('import')">导入 JSON</button>
          <button class="secondary-button" :disabled="model.pending || model.dirty || model.blocked" @click="model.loadChoices">选择仪表盘</button>
          <select v-if="model.choices.length" aria-label="选择仪表盘" :value="model.dashboard.id" :disabled="model.pending || model.blocked" @change="model.choose(($event.target as HTMLSelectElement).value)"><option v-for="choice in model.choices" :key="choice.id" :value="choice.id">{{ choice.title }}{{ choice.is_default ? ' · 首页' : '' }}</option></select>
          <button v-if="model.canEdit && !model.dashboard.is_default" class="secondary-button" :disabled="model.pending || model.dirty || model.blocked" @click="model.makeDefault">设为首页</button>
        </template>
      </div>
    </div>
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
