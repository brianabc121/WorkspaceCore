<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useQueries, useQuery, useQueryClient } from '@tanstack/vue-query'
import { Pencil, Plus, RefreshCw, ShieldAlert } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { dataOf, errorMessage, isAccessError, type Schema } from '../../services/api'
import { useResourceMutation } from '../resources/mutation'
import { permissionsKey } from '../developer/queries'
import ManagementPage from '../../components/management/ManagementPage.vue'
import ManagementSection from '../../components/management/ManagementSection.vue'
import DataState from '../../components/DataState.vue'
import IntegrationStatusPanel from './IntegrationStatusPanel.vue'
import IntegrationChecks from './IntegrationChecks.vue'
import RepositoryPermissionTable from './RepositoryPermissionTable.vue'
import IntegrationConnectionPanel from './IntegrationConnectionPanel.vue'
import { TOKEN_SETTINGS_URL } from './githubValidation'
import { connectionStatus, permissionRows, permissionSummary, type EvidenceState, type TestSnapshot } from './permissions'
import './integrations.css'

// 集成设置（T-0192，效果图 03，方案 docs/v2/ai-admin-redesign 规范 §6 / 契约 §3.2）。
// - 刷新状态只重新读取；测试连接、手动同步、保存是三个独立的写操作（各自带幂等键，CSRF 由 api 客户端加）。
// - 逐仓库四项只来自实际核验（本页测试结果或 /developer/repos/{id}/permissions 快照），没结果写“未核验”。
// - Token 被拒（unauthorized）时不读取、不显示旧的核验结果；被限流时显示预计恢复时间，已有结果照常显示。
type Integration = Schema['IntegrationOut']
type Body = Schema['GitHubIntegrationCreate'] | Schema['IntegrationUpdate']
const auth = useAuthStore(), cache = useQueryClient(), scope = auth.workspaceId!, userId = auth.user!.id, header = { 'X-Workspace-Id': scope }
const query = useQuery({ queryKey: ['integrations', userId, scope], queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/integrations', { params: { header }, signal })) })
const { run, reload, busy, needsReload: locked, accessDenied, error, message: notice } = useResourceMutation(scope, ['integrations', 'projects', 'tasks', 'developer', 'overview'])
const blocked = computed(() => accessDenied.value || isAccessError(query.error.value))
const items = computed(() => blocked.value ? [] : query.data.value?.items ?? [])
// T-0082 移除 Vikunja 后只有 GitHub 连接，每个工作空间最多一个
const item = computed(() => items.value.find((entry) => entry.provider === 'github'))
const unauthorized = computed(() => !!item.value && (item.value.status === 'unauthorized' || item.value.sync.status === 'unauthorized'))

// ---------------------------------------------------------------- 本页的测试结果
// version：测试后连接的版本。之后再保存或再测试，版本就变了，这份结果不再用于表格（以服务端快照为准）
type TestState = { result: Schema['IntegrationTestResult']; name: string; integrationId: string; version?: number | null; checkedAt: string | null; dismissed: boolean }
const testResult = ref<TestState | null>(null)
const testValid = computed(() => {
  const t = testResult.value
  if (!t || blocked.value) return false
  const current = items.value.find((entry) => entry.id === t.integrationId)
  return !current || t.version == null || current.version === t.version
})
const testSnapshot = computed<TestSnapshot | null>(() => testValid.value && item.value && testResult.value!.integrationId === item.value.id ? { repositories: testResult.value!.result.repositories ?? [], checkedAt: testResult.value!.checkedAt } : null)

// ---------------------------------------------------------------- 仓库投影与权限快照（只读）
const readable = computed(() => !!item.value && !blocked.value && !unauthorized.value && !!item.value.repositories?.length)
const projection = useQuery({
  queryKey: computed(() => ['developer', userId, scope, 'integration-repos', item.value?.id ?? ''] as const),
  enabled: readable,
  // 按连接找到各仓库的投影 ID（最多 20 个仓库；多读几页以防投影里还有旧仓库）
  queryFn: async ({ queryKey, signal }) => {
    const found: Schema['RepoOut'][] = []
    let cursor: string | null = null
    for (let page = 0; page < 5; page += 1) {
      const result: Schema['RepoPage'] = dataOf(await auth.api.GET('/api/v1/developer/repos', { params: { header, query: { integration_id: queryKey[4], limit: 100, ...(cursor ? { cursor } : {}) } }, signal }))
      found.push(...result.items)
      cursor = result.next_cursor ?? null
      if (!cursor) break
    }
    return found
  },
})
const matched = computed(() => {
  if (!readable.value || !item.value) return []
  const names = new Set((item.value.repositories ?? []).map((name) => name.toLowerCase()))
  return (projection.data.value ?? []).filter((repo) => repo.integration_id === item.value!.id && names.has(repo.full_name.toLowerCase()))
})
// 查询键与开发者页相同（developer / 用户 / 空间 / permissions / 仓库 ID）：SSE 的 developer 主题一起刷新
const evidence = useQueries({
  queries: () => matched.value.map((repo) => ({
    queryKey: ['developer', userId, scope, 'permissions', repo.id] as const,
    queryFn: async ({ signal }: { signal: AbortSignal }) => dataOf(await auth.api.GET('/api/v1/developer/repos/{repo_id}/permissions', { params: { header, path: { repo_id: repo.id } }, signal })),
  })),
})
const evidenceOf = (repoId: string): EvidenceState | undefined => {
  const index = matched.value.findIndex((repo) => repo.id === repoId), state = index >= 0 ? evidence.value[index] : undefined
  return state ? { data: state.data, pending: state.isPending, error: state.error } : undefined
}
// 失权或 Token 被拒：清掉权限快照与仓库投影的缓存，不再显示旧结果
watch([blocked, unauthorized], ([lost, rejected]) => {
  if (!lost && !rejected) return
  cache.removeQueries({ queryKey: permissionsKey(userId, scope) })
  cache.removeQueries({ queryKey: ['developer', userId, scope, 'integration-repos'] })
}, { immediate: true })

const rows = computed(() => {
  if (!item.value || unauthorized.value) return []
  return permissionRows(item.value.repositories ?? [], matched.value, evidenceOf, testSnapshot.value, readable.value && projection.isPending.value)
})
const status = computed(() => item.value ? connectionStatus(item.value, rows.value) : { text: '', tone: 'muted' as const })
const summary = computed(() => unauthorized.value ? { title: '授权失效', detail: '重新连接后再核验', tone: 'red' as const } : permissionSummary(rows.value))
const projectionError = computed(() => projection.error.value && !isAccessError(projection.error.value) ? errorMessage(projection.error.value) : projection.error.value ? '仓库清单暂不可读' : '')

// ---------------------------------------------------------------- 编辑（右侧一栏）
// base：打开编辑时的连接快照；保存带它的 version，后台刷新不会改掉正在填写的内容（冲突时提示重新载入）
const editing = ref<{ base?: Integration } | null>(null)
const panel = ref<InstanceType<typeof IntegrationConnectionPanel> | null>(null), editButton = ref<HTMLButtonElement | null>(null)
function openEditor(field: 'name' | 'token' = 'name') {
  if (busy.value || locked.value || blocked.value) return
  if (!editing.value) editing.value = { base: item.value }
  void panel.value?.focus(field)
}
function closeEditor() {
  if (busy.value) return
  editing.value = null
  editButton.value?.focus()
}
watch(blocked, (value) => { if (value) { editing.value = null; testResult.value = null } })

// 保存后自动检查权限（T-0091）：新建、换 Token、改仓库或重新启用时；检查通过后后端立即开始全量同步
function needsCheck(body: Body) {
  return 'provider' in body || 'api_token' in body || 'repositories' in body || ('enabled' in body && body.enabled === true)
}
async function save(body: Body) {
  const current = editing.value
  if (!current || !auth.canManage || blocked.value) return
  let saved: Integration | undefined
  const ok = await run(async () => {
    const headers = { 'Idempotency-Key': crypto.randomUUID() }
    if (!current.base) saved = dataOf(await auth.api.POST('/api/v1/integrations', { params: { header }, body: body as Schema['GitHubIntegrationCreate'], headers }))
    else saved = dataOf(await auth.api.PATCH('/api/v1/integrations/{integration_id}', { params: { header, path: { integration_id: current.base.id } }, body: body as Schema['IntegrationUpdate'], headers }))
    // 保存后旧的测试结果作废（Token、仓库清单或启停改变后快照由后端清除）
    editing.value = null; testResult.value = null
  }, '连接已保存')
  if (ok) editButton.value?.focus()
  if (ok && saved && needsCheck(body)) await test(saved, true)
}
async function test(target: Integration, afterSave = false) {
  if (!auth.canManage || blocked.value) return
  const ok = await run(async () => {
    const result = dataOf(await auth.api.POST('/api/v1/integrations/{integration_id}/test', { params: { header, path: { integration_id: target.id } }, headers: { 'Idempotency-Key': crypto.randomUUID() } }))
    testResult.value = { result, name: target.name, integrationId: target.id, checkedAt: null, dismissed: false }
  }, afterSave ? '连接已保存，并已自动检查权限：结果见下方，全部通过时已经开始同步' : '连接测试已完成，请查看各项检查结果')
  // 记下测试后的连接版本与核验时间（服务端的 last_tested_at）；之后版本一变，这份结果就不再用
  const current = items.value.find((entry) => entry.id === target.id)
  if (ok && testResult.value?.integrationId === target.id) testResult.value = { ...testResult.value, version: current?.version ?? null, checkedAt: current?.last_tested_at ?? null }
}
async function sync(target: Integration) {
  if (!auth.canManage || blocked.value) return
  await run(() => auth.api.POST('/api/v1/integrations/{integration_id}/sync', { params: { header, path: { integration_id: target.id } }, headers: { 'Idempotency-Key': crypto.randomUUID() } }), '同步已排队，请稍后刷新状态')
}
// 刷新状态：只重新读取（不测试、不同步）。写入结果未知或冲突后的重新载入还会关掉编辑、丢弃本页测试结果
async function refreshAll() {
  const stale = locked.value || accessDenied.value
  if (await reload() && stale) { editing.value = null; testResult.value = null }
}
</script>

<template>
  <ManagementPage title="集成设置" description="管理 GitHub 认证、仓库权限与同步。" aside-label="GitHub 连接配置" :class="{ 'ig-editing': !!editing }">
    <template #actions>
      <button type="button" class="secondary-button" :disabled="busy || query.isFetching.value" @click="refreshAll"><RefreshCw aria-hidden="true" />刷新状态</button>
      <button v-if="item" ref="editButton" type="button" class="primary-button" :disabled="busy || locked" @click="openEditor()"><Pencil aria-hidden="true" />修改连接</button>
      <button v-else-if="query.data.value && !blocked" ref="editButton" type="button" class="primary-button" :disabled="busy || locked" @click="openEditor()"><Plus aria-hidden="true" />新建 GitHub 连接</button>
    </template>

    <DataState :loading="query.isPending.value" :error="query.error.value" :empty="false" empty-text="" @retry="refreshAll" />
    <div v-if="(error && !editing) || notice || locked" class="ig-feedback">
      <p v-if="error && !editing" role="alert" class="feedback">{{ errorMessage(error) }}</p>
      <p v-if="notice" role="status" class="feedback">{{ notice }}</p>
      <button v-if="locked" type="button" class="secondary-button" :disabled="busy" @click="refreshAll">重新加载连接</button>
    </div>

    <div v-if="item && unauthorized" class="ws-callout ig-reconnect">
      <p><ShieldAlert aria-hidden="true" />GitHub 授权已失效：请更换 Token 后重新测试连接。旧的核验结果已隐藏。</p>
      <button type="button" class="primary-button" :disabled="busy || locked" @click="openEditor('token')">重新连接</button>
    </div>
    <IntegrationStatusPanel v-if="item" :item="item" :status="status" :summary="summary" :busy="busy" :locked="locked" @test="test(item)" @sync="sync(item)" />
    <IntegrationChecks v-if="testResult && testValid && !testResult.dismissed" :result="testResult.result" :name="testResult.name" @close="testResult.dismissed = true" />
    <template v-if="item">
      <RepositoryPermissionTable :rows="rows" :unauthorized="unauthorized" :error="projectionError" @retry="projection.refetch()" />
      <ManagementSection title="修复权限" class="ig-repair">
        <ol class="asv-steps ig-steps">
          <li><strong>在 GitHub 调整细粒度 Token</strong><span>进入 GitHub 的 <a :href="TOKEN_SETTINGS_URL" target="_blank" rel="noopener noreferrer">细粒度 Token 设置</a>，编辑现有 Token；新建的 Token 用“修改连接”换上。</span></li>
          <li><strong>授权目标仓库的只读权限</strong><span>为所选仓库授予只读访问权限（Metadata、Issues、Pull requests、Actions）。</span></li>
          <li><strong>返回测试连接并同步</strong><span>回到本页面，先点“测试连接”，确认权限后再执行“手动同步”。</span></li>
        </ol>
      </ManagementSection>
    </template>
    <ManagementSection v-else-if="query.data.value && !blocked" title="还没有 GitHub 连接" description="每个工作空间可以接入一个 GitHub 连接，只读同步 Issues、Pull requests、Actions；项目与任务由工作台自己管理，不需要连接。">
      <ol class="asv-steps ig-steps">
        <li><strong>生成只读的细粒度 Token</strong><span>点右上角“新建 GitHub 连接”，按右侧的步骤在 GitHub 生成 Token。</span></li>
        <li><strong>粘贴 Token 并选择仓库</strong><span>读取 Token 能访问的仓库，最多勾选 20 个。</span></li>
        <li><strong>保存后自动测试权限</strong><span>逐仓库核验读取权限，通过后开始同步。</span></li>
      </ol>
    </ManagementSection>

    <template v-if="!blocked" #aside>
      <IntegrationConnectionPanel ref="panel" :integration="item" :editing="editing" :busy="busy" :locked="locked" :error="editing ? error : null" :loaded="!!query.data.value" @save="save" @cancel="closeEditor" />
    </template>
  </ManagementPage>
</template>
