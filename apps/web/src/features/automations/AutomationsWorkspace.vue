<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useAuthStore } from '../../stores/auth'
import { useAutomations, useAutomationConnection, useAutomationAccess } from './queries'
import { useResourceMutation } from '../resources/mutation'
import { automationError } from './validation'
import { dataOf, isAccessError, type Schema } from '../../services/api'
import { formatTime } from '../time'
import AutomationEntry from './AutomationEntry.vue'
import ConnectionForm from './ConnectionForm.vue'
import WhitelistForm from './WhitelistForm.vue'
import IntegrationChecks from '../integrations/IntegrationChecks.vue'
import ExternalLink from '../../components/ExternalLink.vue'
const auth = useAuthStore(), access = useAutomationAccess(), scope = auth.workspaceId!, user = auth.user?.id, epoch = access.epoch
const query = useAutomations(scope), connection = useAutomationConnection(scope), mutation = reactive(useResourceMutation(scope, ['automations', 'automation-connection', 'automation-runs', 'overview', 'operations']))
const configuring = ref(false), editing = ref<Schema['AutomationOut'] | 'new' | null>(null), test = ref<Schema['IntegrationTestResult'] | null>(null), refreshing = ref(false)
const active = () => auth.user?.id === user && auth.workspaceId === scope && epoch === access.epoch
const denied = computed(() => access.denied || mutation.accessDenied), error = computed(() => mutation.error ? automationError(mutation.error) : ''), locked = computed(() => denied.value || mutation.busy || mutation.needsReload || refreshing.value || !!query.error.value)
const items = computed(() => denied.value ? [] : [...new Map(query.data.value?.pages.flatMap(p => p.items).map(item => [item.id, item]) ?? []).values()])
const sync = computed(() => query.data.value?.pages[0]?.connection)
async function refresh() {
  if (mutation.busy || refreshing.value || !active()) return
  refreshing.value = true
  try {
    // Authorize fresh data before making old denied caches visible again.
    dataOf(await auth.api.GET('/api/v1/automations', { params: { header: { 'X-Workspace-Id': scope }, query: { limit: 1 } } }))
    if (!active()) return
    access.denied = false
    await mutation.reload()
    if (!active()) return
    configuring.value = false; editing.value = null; test.value = null
  } catch (cause) { if (active()) { mutation.error = cause; if (isAccessError(cause)) access.denied = true } }
  finally { refreshing.value = false }
}
async function saveConnection(body: Schema['AutomationConnectionUpsert']) {
  if (!auth.canManage || locked.value) return
  if (await mutation.run(() => auth.api.PUT('/api/v1/automations/connection', { params: { header: { 'X-Workspace-Id': scope } }, body }))) configuring.value = false
}
async function testConnection() {
  if (!auth.canManage || locked.value) return
  await mutation.run(async () => { const result = dataOf(await auth.api.POST('/api/v1/automations/connection/test', { params: { header: { 'X-Workspace-Id': scope } } })); if (active()) test.value = result }, '连接测试已完成')
}
async function save(body: Schema['AutomationCreate'] | Schema['AutomationUpdate']) {
  if (!auth.canManage || locked.value) return
  const item = editing.value
  const effect = () => item && item !== 'new' ? auth.api.PATCH('/api/v1/automations/{automation_id}', { params: { header: { 'X-Workspace-Id': scope }, path: { automation_id: item.id } }, body: body as Schema['AutomationUpdate'] }) : auth.api.POST('/api/v1/automations', { params: { header: { 'X-Workspace-Id': scope } }, body: body as Schema['AutomationCreate'] })
  if (await mutation.run(effect)) editing.value = null
}
async function remove(item: Schema['AutomationOut']) {
  if (!auth.canManage || locked.value || !window.confirm(`确认将「${item.name}」移出白名单？执行记录会保留。`)) return
  await mutation.run(() => auth.api.DELETE('/api/v1/automations/{automation_id}', { params: { header: { 'X-Workspace-Id': scope }, path: { automation_id: item.id }, query: { version: item.version } } }))
}
</script>
<template>
  <div class="section-heading"><p class="form-help">仅运行白名单工作流。复杂编辑请到 n8n；结果未知时先核对记录。</p><button class="secondary-button" :disabled="mutation.busy || refreshing" @click="refresh">刷新自动化</button></div>
  <p v-if="denied" role="alert">无权查看自动化，请重新登录或联系管理员。</p><p v-else-if="query.isPending.value || refreshing" role="status">正在加载自动化…</p>
  <template v-else>
    <p v-if="query.error.value" role="alert">{{ automationError(query.error.value) }}</p><p v-if="query.error.value && query.data.value || sync?.status === 'stale'" class="feedback">自动化数据可能过期（截至 <time :datetime="sync?.as_of ?? undefined">{{ formatTime(sync?.as_of ?? null) }}</time>）。</p>
    <p v-if="sync?.status === 'not_configured'" class="feedback">尚未配置 n8n，请管理员配置连接。</p><p v-else-if="sync?.status === 'unauthorized'" class="feedback">n8n 授权已失效，请管理员重新连接。</p><p v-else-if="sync?.status === 'unavailable'" class="feedback">n8n 暂不可用，请刷新状态。</p>
    <section v-if="auth.canManage" class="panel automation-connection" aria-label="n8n 连接" role="region"><h2>n8n 连接</h2><p v-if="connection.isPending.value" role="status">正在加载连接…</p><p v-else-if="connection.error.value" role="alert">{{ automationError(connection.error.value) }}</p><template v-else-if="connection.data.value"><p>{{ connection.data.value.configured ? '已配置' : '尚未配置' }} · API Key {{ connection.data.value.api_key_set ? '已设置' : '未设置' }} · 共享密钥 {{ connection.data.value.trigger_secret_set ? '已设置' : '未设置' }}</p><ExternalLink v-if="connection.data.value.public_url" :href="connection.data.value.public_url" label="打开 n8n 编辑器" /><div class="form-actions"><button class="secondary-button" :disabled="locked" @click="configuring = true">配置 n8n 连接</button><button class="secondary-button" :disabled="locked || !connection.data.value.configured" @click="testConnection">测试 n8n 连接</button><button class="secondary-button" :disabled="locked || !connection.data.value.configured" @click="editing = 'new'">登记工作流</button></div></template></section>
    <IntegrationChecks v-if="test && auth.canManage" :result="test" name="n8n" />
    <p v-if="!items.length && !query.error.value">暂无白名单工作流。</p><div class="automation-list"><AutomationEntry v-for="item in items" :key="item.id" :item="item" :connection="sync!" :locked="locked" @edit="editing = item" @remove="remove(item)" /></div><button v-if="query.hasNextPage.value" class="secondary-button" :disabled="query.isFetching.value" @click="query.fetchNextPage()">更多工作流</button>
    <ConnectionForm v-if="configuring && connection.data.value && auth.canManage" :key="connection.data.value.version ?? 0" :connection="connection.data.value" :busy="mutation.busy" :locked="locked" :error="error" @save="saveConnection" @close="configuring = false" @reload="refresh" />
    <WhitelistForm v-if="editing && auth.canManage" :item="editing === 'new' ? undefined : editing" :busy="mutation.busy" :locked="locked" :error="error" @save="save" @close="editing = null" />
  </template>
  <p v-if="error && !configuring && !editing" role="alert">{{ error }}</p><p v-if="mutation.message" role="status">{{ mutation.message }}</p><button v-if="mutation.needsReload && !configuring" class="secondary-button" :disabled="mutation.busy" @click="refresh">重新加载最新版本</button>
</template>
