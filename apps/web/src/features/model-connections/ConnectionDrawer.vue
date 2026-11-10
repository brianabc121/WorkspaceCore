<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { Layers, X } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { ApiError } from '../../services/api'
import { TYPE_LABEL, mcError, useConnection, type Capabilities, type Connection, type CreatePrefill, type DrawerTab } from './api'
import ConnectionForm from './ConnectionForm.vue'
import ConnectionModels from './ConnectionModels.vue'
import ConnectionGrants from './ConnectionGrants.vue'
import ConnectionActivity from './ConnectionActivity.vue'

// 连接详情（效果图右侧一栏；1440 以下放进右侧抽屉，窄屏全屏）：连接配置 / 模型管理 / 智能体授权 / 活动记录四个页签。
// 新建时只有“连接配置”。按完整 ID 读单个连接（不在当前页、当前筛选里也能打开）；不存在或已无权访问时不留旧内容。
const TABS: { key: DrawerTab; name: string }[] = [{ key: 'config', name: '连接配置' }, { key: 'models', name: '模型管理' }, { key: 'grants', name: '智能体授权' }, { key: 'activity', name: '活动记录' }]
const props = defineProps<{ connectionId: string | null; capabilities: Capabilities | undefined; prefill?: CreatePrefill | null; intent?: 'check' | null; aside?: boolean; initialTab?: DrawerTab }>()
const emit = defineEmits<{ close: []; created: [id: string]; copy: [prefill: CreatePrefill]; toggle: [connection: Connection] }>()
const auth = useAuthStore(), scope = auth.workspaceId!
const detail = useConnection(scope, () => props.connectionId)
// 不存在、跨空间或失权（403 / 404 / 422）时不留旧内容；其他刷新失败保留上次取回的内容并说明
const gone = computed(() => detail.error.value instanceof ApiError && [403, 404, 422].includes(detail.error.value.status))
const connection = computed(() => (gone.value || detail.data.value?.id !== props.connectionId ? null : detail.data.value ?? null))
const tab = ref<DrawerTab>(props.initialTab ?? 'config')
watch(() => props.initialTab, value => { if (value) tab.value = value })
const tablist = ref<HTMLElement | null>(null)
// 方向键在页签之间移动（WAI-ARIA tabs）
async function key(event: KeyboardEvent) {
  const index = TABS.findIndex(t => t.key === tab.value)
  const next = event.key === 'ArrowRight' ? index + 1 : event.key === 'ArrowLeft' ? index - 1 : event.key === 'Home' ? 0 : event.key === 'End' ? TABS.length - 1 : -2
  if (next === -2) return
  event.preventDefault()
  tab.value = TABS[(next + TABS.length) % TABS.length]!.key
  await nextTick()
  tablist.value?.querySelector<HTMLElement>('[aria-selected="true"]')?.focus()
}
const title = computed(() => (props.connectionId ? '编辑连接' : '添加连接'))
</script>

<template>
  <section class="mc-drawer" :aria-label="title">
    <div v-if="aside" class="asv-side-head mc-drawer-head">
      <h2>{{ title }}</h2>
      <button type="button" class="icon-button" :aria-label="`关闭：${title}`" @click="emit('close')"><X aria-hidden="true" /></button>
    </div>

    <template v-if="!connectionId">
      <ConnectionForm :connection="null" :capabilities="capabilities" :prefill="prefill" @created="emit('created', $event)" @cancel="emit('close')" />
    </template>
    <template v-else>
      <p v-if="detail.isPending.value" role="status" class="ws-state-line">正在加载连接…</p>
      <div v-else-if="!connection" class="ws-state-line">
        <p role="alert">{{ gone || !detail.error.value ? '连接不存在，或你已无权访问。' : mcError(detail.error.value) }}</p>
        <button type="button" class="secondary-button" @click="emit('close')">关闭</button>
      </div>
      <template v-else>
        <div class="mc-drawer-title">
          <span class="mc-icon" aria-hidden="true"><Layers /></span>
          <h3>{{ connection.name }}</h3>
          <span v-if="connection.connection_type" class="mc-type" :class="`is-${connection.connection_type}`">{{ TYPE_LABEL[connection.connection_type] }}</span>
        </div>
        <div ref="tablist" class="sk-tabs mc-tabs" role="tablist" aria-label="连接详情">
          <button v-for="t in TABS" :id="`mc-tab-${t.key}`" :key="t.key" type="button" role="tab" :aria-selected="tab === t.key" :tabindex="tab === t.key ? 0 : -1" :aria-controls="`mc-panel-${t.key}`" @click="tab = t.key" @keydown="key">{{ t.name }}</button>
        </div>
        <div :id="`mc-panel-${tab}`" role="tabpanel" :aria-labelledby="`mc-tab-${tab}`">
          <ConnectionForm v-if="tab === 'config'" :key="connection.id" :connection="connection" :capabilities="capabilities" :intent="intent" @copy="emit('copy', $event)" @grants="tab = 'grants'" @toggle="emit('toggle', $event)" />
          <ConnectionModels v-else-if="tab === 'models'" :connection="connection" />
          <ConnectionGrants v-else-if="tab === 'grants'" :connection="connection" />
          <ConnectionActivity v-else :connection="connection" />
        </div>
        <p v-if="detail.error.value" class="form-help">刷新失败，显示的是上次取回的内容。</p>
      </template>
    </template>
  </section>
</template>
