<script setup lang="ts">
import { computed, ref } from 'vue'
import { useAuthStore } from '../stores/auth'
import { useModulesStore } from '../stores/modules'
import { ApiError, dataOf, errorMessage, type Schema } from '../services/api'
import { registry, type WidgetType } from '../widgets/registry'

// 应用中心（T-0159，docs/v2/MODULES_PLAN.md D3）：按工作空间启用 / 停用插拔式模块。owner 能改，admin 只读。
// 列表与侧栏共用 modules store，启用 / 停用后侧栏、首页组件立刻跟着变。
type Module = Schema['ModuleOut']
const auth = useAuthStore(), modules = useModulesStore()
const busy = ref<string | null>(null), error = ref(''), notice = ref('')
const items = computed(() => modules.items)
const names = computed(() => new Map(items.value.map((item) => [item.key, item.name])))
const nameOf = (key: string) => names.value.get(key) ?? key
const widgetTitle = (type: string) => registry[type as WidgetType]?.title ?? type
const status = (item: Module) => (!item.ready ? '需要先部署' : item.enabled ? '已启用' : '已停用')
void modules.refresh()

async function toggle(item: Module) {
  if (busy.value) return
  const enabled = !item.enabled
  if (!enabled && !window.confirm(`停用「${item.name}」？停用后入口、首页组件、智能体工具都会隐藏，数据会保留，再启用就恢复。`)) return
  busy.value = item.key; error.value = ''; notice.value = ''
  try {
    modules.apply(dataOf(await auth.api.PATCH('/api/v1/modules/{key}', { params: { header: { 'X-Workspace-Id': auth.workspaceId! }, path: { key: item.key } }, body: { enabled } })))
    notice.value = `「${item.name}」已${enabled ? '启用' : '停用'}。`
  } catch (cause) {
    // 依赖检查（409 MODULE_DEPENDENCY）的说明由后端写好，直接显示
    error.value = cause instanceof ApiError && cause.status === 409 ? cause.message : errorMessage(cause)
  } finally {
    busy.value = null
    void modules.refresh()
  }
}
</script>

<template>
  <div class="page-heading"><h1>应用中心</h1><p>按工作空间启用或停用功能模块。停用后入口、首页组件、智能体工具都会隐藏，数据保留，再启用就恢复。</p></div>
  <p v-if="!auth.canManage" role="alert">只有 owner / admin 能查看应用中心。</p>
  <template v-else>
    <p v-if="!auth.isOwner" class="feedback" role="note">你可以查看；只有 owner 能启用或停用。</p>
    <p v-if="error" role="alert" class="feedback">{{ error }}</p>
    <p v-if="notice" role="status" class="feedback">{{ notice }}</p>
    <p v-if="!modules.loaded" role="status">正在加载…</p>
    <div v-else-if="modules.error && !items.length" class="data-state"><p role="alert">{{ errorMessage(modules.error) }}</p><button type="button" class="secondary-button" @click="modules.refresh()">重试</button></div>
    <p v-else-if="!items.length" class="empty-state">还没有可以启用的模块。</p>
    <ul class="app-list">
      <li v-for="item in items" :key="item.key" class="panel app-card" :aria-label="`模块：${item.name}`">
        <div class="app-card-head">
          <h2>{{ item.name }} <span class="badge">v{{ item.version }}</span> <span class="badge" :class="`app-status-${item.ready ? (item.enabled ? 'on' : 'off') : 'deploy'}`">{{ status(item) }}</span></h2>
          <button v-if="item.can_manage" type="button" role="switch" class="app-switch" :aria-checked="item.enabled" :aria-label="`启用${item.name}`" :disabled="!!busy" @click="toggle(item)">
            <span class="app-switch-track" aria-hidden="true"><span class="app-switch-thumb"></span></span>
            <span>{{ busy === item.key ? '正在保存…' : item.enabled ? '已启用' : '已停用' }}</span>
          </button>
        </div>
        <p class="app-description">{{ item.description }}</p>
        <p v-if="!item.ready" class="agent-notice" role="note">需要先部署：服务器的 .env 里还没有 <template v-for="(name, index) in item.missing" :key="name">{{ index ? '、' : '' }}<code>{{ name }}</code></template>。用部署脚本安装对应组件（或在 .env 里填上）后重启工作台；没部署时也能启用，页面会提示未部署。</p>
        <dl class="app-facts">
          <div><dt>依赖</dt><dd>{{ item.depends_on.length ? item.depends_on.map(nameOf).join('、') : '无' }}</dd></div>
          <div v-if="item.dependents.length"><dt>被依赖</dt><dd>{{ item.dependents.map(nameOf).join('、') }}（它们启用时不能停用）</dd></div>
          <div><dt>智能体工具</dt><dd><template v-if="item.tools.length"><code v-for="tool in item.tools" :key="tool" class="app-chip">{{ tool }}</code></template><template v-else>无</template></dd></div>
          <div><dt>首页组件</dt><dd>{{ item.widgets.length ? item.widgets.map(widgetTitle).join('、') : '无' }}</dd></div>
        </dl>
      </li>
    </ul>
  </template>
</template>

<style scoped>
.app-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 16px; min-width: 0; }
.app-card { padding: 20px 24px; min-width: 0; overflow-wrap: anywhere; display: grid; gap: 10px; }
.app-card-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px; }
.app-card-head h2 { margin: 0; font-size: 1.1rem; display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.app-description { color: var(--muted); font-size: .88rem; }
.app-status-on { color: var(--success); border-color: var(--success); background: var(--success-soft); }
.app-status-deploy { color: var(--warning); border-color: var(--warning); background: var(--warning-soft); }
.app-facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 8px 16px; margin: 0; }
.app-facts dt { color: var(--muted); font-size: .8rem; }
.app-facts dd { margin: 2px 0 0; display: flex; flex-wrap: wrap; gap: 4px; font-size: .88rem; }
.app-chip { border: 1px solid var(--line); border-radius: 6px; padding: 1px 6px; font-size: .78rem; background: var(--bg); }
.app-switch { display: inline-flex; align-items: center; gap: 8px; border: 0; background: none; padding: 4px 0; color: var(--text); font-size: .85rem; }
.app-switch:disabled { opacity: .6; }
.app-switch-track { position: relative; width: 38px; height: 22px; border-radius: 11px; background: var(--line); transition: background .15s; flex-shrink: 0; }
.app-switch-thumb { position: absolute; top: 3px; left: 3px; width: 16px; height: 16px; border-radius: 50%; background: var(--surface); box-shadow: var(--shadow); transition: transform .15s; }
.app-switch[aria-checked="true"] .app-switch-track { background: var(--accent); }
.app-switch[aria-checked="true"] .app-switch-thumb { transform: translateX(16px); }
.app-switch:focus-visible { outline: 3px solid var(--accent); outline-offset: 3px; border-radius: 6px; }
@media (max-width: 760px) { .app-card { padding: 16px; } }
</style>
