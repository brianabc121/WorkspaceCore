<script setup lang="ts">
import { computed, ref } from 'vue'
import { useQueryClient } from '@tanstack/vue-query'
import { ArrowRight } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { dataOf, type Schema } from '../../services/api'
import { formatTime } from '../time'
import { useAgentAction } from './action'
import { applyStatus, bindingError, bindingKey, inFlight, reasonText, useModelBinding, type ModelChoice } from './modelBinding'
import ModelConnectionPicker from './ModelConnectionPicker.vue'

// 智能体的主模型（T-0207，契约第 3 节）：目标（保存的连接 + 模型）与已应用（实例确认在用的）分开显示。
// 保存只是改目标：实例每分钟取一次配置，校验写入后回报才算已应用；失败 / 状态待确认 / 待手动时保留上一次已应用的信息，
// 可以“重新应用”（同一目标复用同一个任务，尝试次数加 1）。待应用 / 应用中时每 5 秒刷新，离开页面就停止。
const props = defineProps<{ instance: Schema['AgentInstanceOut'] }>()
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id, cache = useQueryClient()
const { busy, error, notice, act, header } = useAgentAction(scope, bindingError)
const view = useModelBinding(scope, () => props.instance.id)
const binding = computed(() => view.data.value?.binding ?? null)
const state = computed(() => view.data.value?.apply_state ?? null)
const status = computed(() => applyStatus(state.value))
const editing = ref(false)
const choice = ref<ModelChoice | null>(null)
const switching = computed(() => !!binding.value && !binding.value.revoked && binding.value.applied_revision !== binding.value.desired_revision)
const sameAsTarget = computed(() => !!choice.value && !!binding.value && !binding.value.revoked && choice.value.connection_id === binding.value.connection_id && choice.value.model_id === binding.value.model_id)

function startEdit() {
  const b = binding.value
  choice.value = b && !b.revoked && b.connection_id && b.model_id ? { connection_id: b.connection_id, model_id: b.model_id } : null
  editing.value = true
}
const store = (next: Schema['ModelBindingView']) => cache.setQueryData(bindingKey(user, scope, props.instance.id), next)

async function save() {
  const value = choice.value
  if (!value || sameAsTarget.value) return
  const ok = await act(async () => {
    const out = dataOf(await auth.api.PUT('/api/v1/agents/instances/{instance_id}/model-binding', { params: { header, path: { instance_id: props.instance.id } }, body: { version: binding.value?.version ?? null, connection_id: value.connection_id, model_id: value.model_id } }))
    store({ binding: out, apply_state: out.apply_state, legacy: null })
  }, '已保存：实例取走新配置并确认后才显示“已应用”。')
  if (ok) editing.value = false
}
// 应用失败 / 状态待确认时可以改回实例确认在用的那组连接与模型（只是把目标改回去，不承诺恢复已更换或已撤销的 Key）
const canRevert = computed(() => {
  const b = binding.value
  return !!b && !!b.applied_connection_id && !!b.applied_model_id && !inFlight(state.value)
    && (b.revoked || b.applied_connection_id !== b.connection_id || b.applied_model_id !== b.model_id)
})
async function revert() {
  const b = binding.value
  if (!b?.applied_connection_id || !b.applied_model_id) return
  const target = { connection_id: b.applied_connection_id, model_id: b.applied_model_id }
  await act(async () => {
    const out = dataOf(await auth.api.PUT('/api/v1/agents/instances/{instance_id}/model-binding', { params: { header, path: { instance_id: props.instance.id } }, body: { version: b.version, ...target } }))
    store({ binding: out, apply_state: out.apply_state, legacy: null })
  }, '已改回上一次已应用的连接与模型：实例确认后显示“已应用”。')
}
async function apply() {
  const b = binding.value
  if (!b) return
  await act(async () => { dataOf(await auth.api.POST('/api/v1/agents/instances/{instance_id}/model-binding/apply', { params: { header, path: { instance_id: props.instance.id } }, body: { version: b.version } })) }, '已重新排队：实例下一次取配置时应用。')
}
async function remove() {
  const b = binding.value
  if (!b || !window.confirm(`解除「${props.instance.name}」的主模型？\n\n解除后它立即不能再取这条连接的 Key，并移除受管的模型配置；已在 OpenClaw 进程里的 Key 不保证立即失效。连接本身和授权不会删除。`)) return
  await act(async () => {
    const out = dataOf(await auth.api.DELETE('/api/v1/agents/instances/{instance_id}/model-binding', { params: { header, path: { instance_id: props.instance.id }, query: { version: b.version } } }))
    store({ binding: out, apply_state: out.apply_state, legacy: null })
  }, '已解除：实例确认移除配置后显示“已应用”。')
}
</script>

<template>
  <section class="agent-section mc-binding" aria-label="主模型">
    <div class="section-heading">
      <h3>主模型 <span v-if="view.isSuccess.value" class="asv-state" :class="`tone-${status.tone}`">{{ status.label }}</span></h3>
      <RouterLink class="asv-go" to="/settings/model-connections">中转站管理<ArrowRight aria-hidden="true" /></RouterLink>
    </div>
    <p v-if="view.isPending.value" role="status">正在读取主模型…</p>
    <p v-else-if="view.isError.value" role="alert">{{ bindingError(view.error.value) }}</p>
    <template v-else>
      <dl v-if="binding" class="mc-binding-facts">
        <div><dt>目标</dt><dd>{{ binding.revoked ? '已解除（不使用模型）' : `${binding.connection_name ?? '（连接已删除）'} · ${binding.model_id}` }}<span class="asv-sub">第 {{ binding.desired_revision }} 版</span></dd></div>
        <div>
          <dt>已应用</dt>
          <dd>
            <template v-if="binding.applied_revision !== null">{{ binding.applied_connection_id ? `${binding.applied_connection_name ?? '（连接已删除）'} · ${binding.applied_model_id}` : '已移除受管模型' }}<span class="asv-sub">第 {{ binding.applied_revision }} 版 · {{ formatTime(binding.applied_at) }}</span></template>
            <template v-else>还没有确认过</template>
          </dd>
        </div>
      </dl>
      <p v-else-if="view.data.value?.legacy" class="form-help">旧配置：接入包里的模型引用 {{ view.data.value.legacy.legacy_refs.join('、') }}。工作台不猜它用的是哪条连接；选一个主模型后自动换成中转站管理的连接。</p>
      <p class="form-help">{{ status.hint }}<template v-if="switching && binding?.applied_connection_id"> 切换确认前，旧连接仍可取 Key。</template></p>
      <p v-if="binding?.last_error_code" class="feedback" role="note">原因：{{ reasonText(binding.last_error_code) }}</p>

      <div v-if="!editing" class="form-actions">
        <button type="button" class="secondary-button" :disabled="busy" @click="startEdit">{{ binding && !binding.revoked ? '更换连接或模型' : '选择主模型' }}</button>
        <button v-if="binding && !inFlight(state) && state !== 'applied'" type="button" class="secondary-button" :disabled="busy" @click="apply">重新应用</button>
        <button v-if="canRevert" type="button" class="secondary-button" :disabled="busy" @click="revert">改回已应用的配置</button>
        <button v-if="binding && !binding.revoked" type="button" class="secondary-button danger" :disabled="busy" @click="remove">解除</button>
      </div>
      <form v-else class="mc-binding-form" aria-label="选择主模型" @submit.prevent="save">
        <ModelConnectionPicker v-model="choice" :instance-id="instance.id" :disabled="busy" />
        <div class="form-actions">
          <button type="button" class="secondary-button" :disabled="busy" @click="editing = false">取消</button>
          <button type="submit" class="primary-button" :disabled="busy || !choice || sameAsTarget">{{ busy ? '正在保存…' : '保存' }}</button>
        </div>
      </form>
    </template>
    <p v-if="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
  </section>
</template>
