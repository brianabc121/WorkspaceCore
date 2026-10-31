<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { dataOf, errorMessage, isAccessError, type Schema } from '../../services/api'
import { useResourceMutation } from '../resources/mutation'
import { formatTime } from '../time'
import DataState from '../../components/DataState.vue'
import SyncNotice from '../../components/SyncNotice.vue'
import ModalPanel from '../../components/ModalPanel.vue'
import IntegrationForm from './IntegrationForm.vue'
import GitHubForm from './GitHubForm.vue'
import IntegrationChecks from './IntegrationChecks.vue'
const auth = useAuthStore(), scope = auth.workspaceId!, userId = auth.user!.id, header = { 'X-Workspace-Id': scope }
const query = useQuery({ queryKey: ['integrations', userId, scope], queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/integrations', { params: { header }, signal })) })
const { run, reload: refresh, busy, needsReload: locked, accessDenied, error, message: notice } = useResourceMutation(scope, ['integrations', 'projects', 'tasks', 'developer', 'overview'])
const blocked = computed(() => accessDenied.value || isAccessError(query.error.value)), items = computed(() => blocked.value ? [] : query.data.value?.items ?? [])
const editing = ref<Schema['IntegrationOut'] | 'vikunja' | 'github' | null>(null)
const provider = computed(() => typeof editing.value === 'string' ? editing.value : editing.value?.provider)
const existing = computed(() => typeof editing.value === 'object' && editing.value ? editing.value : undefined)
const testResult = ref<{ result: Schema['IntegrationTestResult']; name: string } | null>(null)
const statusLabels = { pending: '待测试', active: '已连接', unauthorized: '授权失效', unavailable: '暂不可用', disabled: '已停用' }
watch(blocked, (value) => { if (value) { editing.value = null; testResult.value = null } })
async function save(body: Schema['IntegrationCreate'] | Schema['GitHubIntegrationCreate'] | Schema['IntegrationUpdate']) {
  const current = editing.value
  if (!current || !auth.canManage || blocked.value) return
  await run(async () => {
    const headers = { 'Idempotency-Key': crypto.randomUUID() }
    if (typeof current === 'string') dataOf(await auth.api.POST('/api/v1/integrations', { params: { header }, body: body as Schema['IntegrationCreate'] | Schema['GitHubIntegrationCreate'], headers }))
    else dataOf(await auth.api.PATCH('/api/v1/integrations/{integration_id}', { params: { header, path: { integration_id: current.id } }, body: body as Schema['IntegrationUpdate'], headers }))
    editing.value = null; testResult.value = null
  }, '连接已保存，请测试连接')
}
async function test(item: Schema['IntegrationOut']) {
  if (!auth.canManage || blocked.value) return
  await run(async () => { testResult.value = { name: item.name, result: dataOf(await auth.api.POST('/api/v1/integrations/{integration_id}/test', { params: { header, path: { integration_id: item.id } }, headers: { 'Idempotency-Key': crypto.randomUUID() } })) } }, '连接测试已完成，请查看各项检查结果')
}
async function sync(item: Schema['IntegrationOut']) {
  if (!auth.canManage || blocked.value) return
  await run(() => auth.api.POST('/api/v1/integrations/{integration_id}/sync', { params: { header, path: { integration_id: item.id } }, headers: { 'Idempotency-Key': crypto.randomUUID() } }), '同步已排队，请稍后刷新状态')
}
async function reload() { if (await refresh()) { editing.value = null; testResult.value = null } }
</script>
<template>
  <div class="task-toolbar"><p class="form-help">管理员配置连接，每个工作空间最多一个 Vikunja 和一个 GitHub 连接。</p><button class="secondary-button" :disabled="busy || query.isFetching.value" @click="reload">刷新连接</button></div>
  <DataState :loading="query.isPending.value" :error="query.error.value" :empty="!items.length && !blocked" empty-text="尚未配置外部服务连接" @retry="reload" />
  <p v-if="error" role="alert" class="feedback">{{ errorMessage(error) }}</p><p v-if="notice" role="status" class="feedback">{{ notice }}</p>
  <button v-if="locked" class="secondary-button" :disabled="busy" @click="reload">重新加载连接</button>
  <template v-if="!blocked && auth.canManage">
    <div v-if="query.data.value" class="form-actions"><button v-if="!items.some(item => item.provider === 'vikunja')" class="primary-button" :disabled="busy || locked" @click="editing = 'vikunja'">新建连接</button><button v-if="!items.some(item => item.provider === 'github')" class="primary-button" :disabled="busy || locked" @click="editing = 'github'">新建 GitHub 连接</button></div>
    <section v-for="item in items" :key="item.id" class="panel form-panel integration-card">
      <h2>{{ item.name }}</h2><p>{{ item.provider === 'github' ? 'GitHub' : 'Vikunja' }} · 状态：{{ statusLabels[item.status] }} · Token {{ item.token_set ? '已设置' : '未设置' }}</p><dl><dt>已保存的服务地址</dt><dd>{{ item.base_url }}</dd><dt>浏览器访问地址</dt><dd>{{ item.public_url }}</dd></dl><p v-if="item.provider === 'github'">仓库：{{ item.repositories?.join(' · ') }}</p><p>核验版本：{{ item.tested_version }} · API {{ item.api_version }} · 实例版本：{{ item.instance_version ?? '尚未测试' }}</p><p>Token 到期：{{ formatTime(item.token_expires_at) }} · 最近测试：{{ formatTime(item.last_tested_at) }}</p><p>Webhook：{{ item.provider === 'github' ? 'GitHub V1 不支持，使用周期同步' : item.webhooks.mode === 'auto' ? '自动注册 ' + item.webhooks.registered_projects + ' 个项目' : '暂不可用，使用周期对账' }}</p>
      <SyncNotice :sync="item.sync" :provider="item.provider" /><div class="form-actions"><button class="secondary-button" :disabled="busy || locked" @click="editing = item">修改连接</button><button class="secondary-button" :disabled="busy || locked" @click="test(item)">测试连接</button><button class="secondary-button" :disabled="busy || locked || item.status === 'disabled' || item.sync.reason === 'rate_limited' || item.status === 'unauthorized'" @click="sync(item)">手动同步</button></div>
    </section>
  </template>
  <IntegrationChecks v-if="testResult && !blocked" :result="testResult.result" :name="testResult.name" />
  <ModalPanel v-if="editing && !blocked" :title="provider === 'github' ? 'GitHub 连接配置' : 'Vikunja 连接配置'" :busy="busy" @close="!busy && (editing = null)"><GitHubForm v-if="provider === 'github'" :integration="existing" :busy="busy" :locked="locked" @save="save" /><IntegrationForm v-else :integration="existing" :busy="busy" :locked="locked" @save="save" /><p v-if="error" role="alert">{{ errorMessage(error) }}</p></ModalPanel>
</template>
