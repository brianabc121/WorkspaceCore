<script setup lang="ts">
import { computed, ref } from 'vue'
import { useAuthStore } from '../../stores/auth'
import type { Schema } from '../../services/api'
import { agentError, downloadBundle, unique, useAgentSettings, useInstances, useSecrets } from './queries'
import { useAgentAction } from './action'
import InstanceCard from './InstanceCard.vue'
import InstanceForm from './InstanceForm.vue'
import { useCreateIntent } from '../../composables/useCreateIntent'

const auth = useAuthStore(), scope = auth.workspaceId!
const settings = useAgentSettings(scope), instances = useInstances(scope), secrets = useSecrets(scope)
const { busy, error, notice, act, header } = useAgentAction(scope)
const editing = ref<Schema['AgentInstanceOut'] | 'new' | null>(null)
const items = computed(() => unique(instances.data.value?.pages))
const secretItems = computed(() => unique(secrets.data.value?.pages))
const path = (instance: Schema['AgentInstanceOut']) => ({ instance_id: instance.id })
// 顶栏“创建 → 登记 OpenClaw 实例”（规范 §8.1）
useCreateIntent(() => !busy.value, () => { editing.value = 'new' })

function toggleAll() {
  const s = settings.data.value
  if (!s) return
  if (s.agents_enabled && !window.confirm('关闭总开关后所有实例立即失去访问权限（MCP、取密钥、IM），确认关闭？')) return
  void act(() => auth.api.PATCH('/api/v1/agents/settings', { params: { header }, body: { agents_enabled: !s.agents_enabled, version: s.version } }), s.agents_enabled ? '总开关已关闭' : '总开关已打开')
}
async function save(body: Schema['AgentInstanceCreate'] | Schema['AgentInstanceUpdate']) {
  const target = editing.value
  const ok = await act(() => target && target !== 'new'
    ? auth.api.PATCH('/api/v1/agents/instances/{instance_id}', { params: { header, path: path(target) }, body: body as Schema['AgentInstanceUpdate'] })
    : auth.api.POST('/api/v1/agents/instances', { params: { header }, body: body as Schema['AgentInstanceCreate'] }), target === 'new' ? '实例已登记' : '实例已保存')
  if (ok) editing.value = null
}
function toggle(instance: Schema['AgentInstanceOut']) {
  const disabling = instance.status !== 'disabled'
  if (disabling && !window.confirm(`停用「${instance.name}」？它会立即失去访问权限并被踢下线。`)) return
  void act(() => disabling
    ? auth.api.POST('/api/v1/agents/instances/{instance_id}/disable', { params: { header, path: path(instance) }, body: { version: instance.version } })
    : auth.api.POST('/api/v1/agents/instances/{instance_id}/enable', { params: { header, path: path(instance) }, body: { version: instance.version } }), disabling ? '实例已停用' : '实例已启用')
}
function bundle(instance: Schema['AgentInstanceOut'], previous: Schema['BundleRequest']['previous']) {
  void act(() => downloadBundle(scope, instance.id, { version: instance.version, previous }), '接入包已生成并下载：凭证只在这个 zip 里，按里面的 CHECKLIST.md 部署')
}
function revoke(instance: Schema['AgentInstanceOut'], credential: Schema['AgentCredentialOut']) {
  if (!window.confirm('立即吊销这份凭证？使用它的 OpenClaw 会马上失去访问权限。')) return
  void act(() => auth.api.POST('/api/v1/agents/instances/{instance_id}/credentials/{credential_id}/revoke', { params: { header, path: { ...path(instance), credential_id: credential.id } }, body: { version: instance.version } }), '凭证已吊销')
}
function grants(instance: Schema['AgentInstanceOut'], list: Schema['SecretGrantIn'][]) {
  void act(() => auth.api.PUT('/api/v1/agents/instances/{instance_id}/secret-grants', { params: { header, path: path(instance) }, body: { grants: list, version: instance.version } }), '密钥授权已保存')
}
</script>

<template>
  <section class="panel agent-switch" aria-label="总开关">
    <p v-if="settings.isPending.value" role="status">正在加载…</p>
    <p v-else-if="settings.error.value" role="alert">{{ agentError(settings.error.value) }}</p>
    <div v-else-if="settings.data.value" class="section-heading">
      <p><strong>总开关：{{ settings.data.value.agents_enabled ? '打开' : '关闭' }}</strong>。关闭后所有实例立即失去访问权限。</p>
      <button type="button" class="secondary-button" :class="{ 'im-danger': settings.data.value.agents_enabled }" :disabled="busy" @click="toggleAll">{{ settings.data.value.agents_enabled ? '关闭总开关' : '打开总开关' }}</button>
    </div>
  </section>
  <div class="section-heading"><p class="form-help">实例部署在别的服务器上，用接入包里的凭证主动连回工作台。</p><button type="button" class="primary-button" :disabled="busy" @click="editing = 'new'">登记实例</button></div>
  <p v-if="error && !editing" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
  <p v-if="instances.isPending.value" role="status">正在加载实例…</p>
  <p v-else-if="instances.error.value" role="alert">{{ agentError(instances.error.value) }}</p>
  <p v-else-if="!items.length" class="empty-state">还没有实例。点“登记实例”开始。</p>
  <div class="agent-list">
    <InstanceCard
      v-for="instance in items" :key="instance.id" :instance="instance" :secrets="secretItems" :busy="busy"
      @edit="editing = instance" @toggle="toggle(instance)" @bundle="bundle(instance, $event)" @revoke="revoke(instance, $event)" @grants="grants(instance, $event)"
    />
  </div>
  <button v-if="instances.hasNextPage.value" type="button" class="secondary-button" :disabled="instances.isFetchingNextPage.value" @click="instances.fetchNextPage()">更多实例</button>
  <InstanceForm v-if="editing" :instance="editing === 'new' ? undefined : editing" :busy="busy" :error="error" @close="editing = null" @save="save" />
</template>
