<script setup lang="ts">
import { ref } from 'vue'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf, errorMessage, isAccessError, type Schema } from '../../services/api'
import { formatTime } from '../time'
import DataState from '../../components/DataState.vue'
import SyncNotice from '../../components/SyncNotice.vue'
import ModalPanel from '../../components/ModalPanel.vue'
import IntegrationForm from './IntegrationForm.vue'
const auth = useAuthStore(), cache = useQueryClient()
const scope = auth.workspaceId!, userId = auth.user!.id, header = { 'X-Workspace-Id': scope }
const query = useQuery({ queryKey: ['integrations', userId, scope], queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/integrations', { params: { header }, signal })) })
const editing = ref<Schema['IntegrationOut'] | 'new' | null>(null)
const busy = ref(false), locked = ref(false), error = ref(''), notice = ref('')
const testResult = ref<Schema['IntegrationTestResult'] | null>(null)
const statusLabels = { pending: '待测试', active: '已连接', unauthorized: '授权失效', unavailable: '暂不可用', disabled: '已停用' }
function invalidate() { void cache.invalidateQueries({ predicate: (q) => ['integrations', 'projects', 'tasks', 'overview'].includes(String(q.queryKey[0])) && q.queryKey[1] === userId && q.queryKey[2] === scope }) }
async function run(action: (key: string) => Promise<void>, message: string) {
  if (busy.value || locked.value || !auth.canManage) return
  busy.value = true; error.value = ''; notice.value = ''
  try { await action(crypto.randomUUID()); notice.value = message; invalidate() }
  catch (cause) { error.value = errorMessage(cause instanceof ApiError ? cause : new ApiError(0, 'WRITE_UNKNOWN', '')); if (!(cause instanceof ApiError) || cause.status === 0 || cause.status === 409 || cause.status >= 500) locked.value = true }
  finally { busy.value = false }
}
async function save(body: Schema['IntegrationCreate'] | Schema['IntegrationUpdate']) {
  const current = editing.value
  if (!current) return
  await run(async (key) => {
    if (current === 'new') await auth.api.POST('/api/v1/integrations', { params: { header }, body: body as Schema['IntegrationCreate'], headers: { 'Idempotency-Key': key } })
    else await auth.api.PATCH('/api/v1/integrations/{integration_id}', { params: { header, path: { integration_id: current.id } }, body: body as Schema['IntegrationUpdate'], headers: { 'Idempotency-Key': key } })
    editing.value = null; testResult.value = null
  }, '连接已保存，请测试连接')
}
async function test(id: string) {
  await run(async (key) => { testResult.value = dataOf(await auth.api.POST('/api/v1/integrations/{integration_id}/test', { params: { header, path: { integration_id: id } }, headers: { 'Idempotency-Key': key } })) }, '连接测试已完成，请查看各项检查结果')
}
async function sync(id: string) {
  await run(async (key) => { await auth.api.POST('/api/v1/integrations/{integration_id}/sync', { params: { header, path: { integration_id: id } }, headers: { 'Idempotency-Key': key } }) }, '同步已排队，请稍后刷新状态')
}
async function reload() {
  if (busy.value) return
  const result = await query.refetch()
  if (!result.error) { locked.value = false; error.value = ''; editing.value = null; testResult.value = null }
}
</script>
<template>
  <div class="task-toolbar"><p class="form-help">Vikunja API Token 由部署管理员配置，每个工作空间最多一个连接。</p><button class="secondary-button" :disabled="busy || query.isFetching.value" @click="reload">刷新连接</button></div>
  <DataState :loading="query.isPending.value" :error="query.error.value" :empty="!query.data.value?.items.length" empty-text="尚未配置 Vikunja 连接" @retry="reload" />
  <p v-if="error" role="alert" class="feedback">{{ error }}</p><p v-if="notice" role="status" class="feedback">{{ notice }}</p>
  <button v-if="locked" class="secondary-button" :disabled="busy" @click="reload">重新加载连接</button>
  <template v-if="!isAccessError(query.error.value)">
    <button v-if="query.data.value && !query.data.value.items.length" class="primary-button" :disabled="busy || locked" @click="editing = 'new'; error = ''">新建连接</button>
    <section v-for="item in query.data.value?.items ?? []" :key="item.id" class="panel form-panel integration-card">
      <h2>{{ item.name }}</h2><p>状态：{{ statusLabels[item.status] }} · Token {{ item.token_set ? '已设置' : '未设置' }}</p><p>核验版本：{{ item.tested_version }} · API {{ item.api_version }} · 实例版本：{{ item.instance_version ?? '尚未测试' }}</p><p>Token 到期：{{ formatTime(item.token_expires_at) }} · 最近测试：{{ formatTime(item.last_tested_at) }}</p><p>Webhook：{{ item.webhooks.mode === 'auto' ? `自动注册 ${item.webhooks.registered_projects} 个项目` : '暂不可用，使用周期对账' }}</p>
      <SyncNotice :sync="item.sync" /><div class="form-actions"><button class="secondary-button" :disabled="busy || locked" @click="editing = item; error = ''">修改连接</button><button class="secondary-button" :disabled="busy || locked" @click="test(item.id)">测试连接</button><button class="secondary-button" :disabled="busy || locked || item.status === 'disabled'" @click="sync(item.id)">手动同步</button></div>
    </section>
  </template>
  <section v-if="testResult" class="panel form-panel"><h2>{{ testResult.ok ? '必需检查通过' : '连接测试未通过' }}</h2><p v-if="!testResult.compatible">实例版本与核验版本不一致，请确认兼容性。</p><ul><li v-for="check in testResult.checks" :key="check.name">{{ ({ reachable: '服务可达', version: '版本信息', token: 'Token', tasks_read: '任务读取', webhooks: 'Webhook' })[check.name] }}：{{ check.ok ? '通过' : '未通过' }}</li></ul><p v-for="warning in testResult.warnings" :key="warning">{{ warning }}</p></section>
  <ModalPanel v-if="editing" title="Vikunja 连接配置" :busy="busy" @close="!busy && (editing = null)"><IntegrationForm :integration="editing === 'new' ? undefined : editing" :busy="busy" :locked="locked" @save="save" /><p v-if="error" role="alert">{{ error }}</p><button v-if="locked" class="secondary-button" @click="reload">重新加载连接</button></ModalPanel>
</template>
