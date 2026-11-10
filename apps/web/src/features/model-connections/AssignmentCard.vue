<script setup lang="ts">
import { computed, ref } from 'vue'
import { Bot } from '@lucide/vue'
import { useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { dataOf } from '../../services/api'
import ManagementSection from '../../components/management/ManagementSection.vue'
import ModalPanel from '../../components/ModalPanel.vue'
import { useAgentAction } from '../agents/action'
import { applyStatus, bindingError, bindingKey, reasonText, type ModelChoice } from '../agents/modelBinding'
import ModelConnectionPicker from '../agents/ModelConnectionPicker.vue'
import { useAssignments, type Assignment } from './api'

// OpenClaw 连接分配（规范第 4.3 节）：每个智能体的目标连接与模型、实例确认已应用的连接与模型、应用状态。
// 保存只改目标：实例取走新配置并回报后才是“已应用”；两者不同时都显示。还没设置主模型的显示“未设置”。
// “更换连接”用部署抽屉同一个选择器：只能选已授权给这个智能体、启用且配置完整的连接。
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id, cache = useQueryClient()
const assignments = useAssignments(scope)
const rows = computed(() => [...new Map((assignments.data.value?.pages ?? []).flatMap(p => p.items).map(r => [r.instance_id, r])).values()])
const total = computed(() => assignments.data.value?.pages[0]?.total)
const { busy, error, notice, act, header } = useAgentAction(scope, bindingError)

const editing = ref<Assignment | null>(null)
const choice = ref<ModelChoice | null>(null)
const target = (r: Assignment) => (r.binding && !r.binding.revoked ? r.binding : null)
const differs = (r: Assignment) => !!r.binding && r.binding.applied_revision !== r.binding.desired_revision
const state = (r: Assignment) => applyStatus(r.binding?.apply_state ?? null)
function open(r: Assignment) {
  // 预填当前目标（没有目标时从空开始）；换连接时选择器只清空模型、保留新选的连接
  const t = target(r)
  choice.value = t?.connection_id && t.model_id ? { connection_id: t.connection_id, model_id: t.model_id } : null
  error.value = ''; notice.value = ''
  editing.value = r
}
function close() { if (!busy.value) { editing.value = null; error.value = '' } }
const same = computed(() => {
  const b = editing.value ? target(editing.value) : null
  return !!b && !!choice.value && b.connection_id === choice.value.connection_id && b.model_id === choice.value.model_id
})
async function save() {
  const r = editing.value, value = choice.value
  if (!r || !value || same.value) return
  const ok = await act(async () => {
    const out = dataOf(await auth.api.PUT('/api/v1/agents/instances/{instance_id}/model-binding', { params: { header, path: { instance_id: r.instance_id } }, body: { version: r.binding?.version ?? null, connection_id: value.connection_id, model_id: value.model_id } }))
    cache.setQueryData(bindingKey(user, scope, r.instance_id), { binding: out, apply_state: out.apply_state })
  }, `已保存「${r.instance_name}」的主模型：实例取走新配置并确认后才显示“已应用”。`)
  if (ok) editing.value = null
}
</script>

<template>
  <ManagementSection title="OpenClaw 连接分配" description="按智能体选择连接与模型。保存后由智能体取走新配置并确认，确认前仍按已应用的配置运行。">
    <template #title>OpenClaw 连接分配<template v-if="typeof total === 'number'"> · {{ total }}</template></template>
    <p v-if="assignments.isPending.value" role="status" class="ws-state-line">正在加载分配…</p>
    <div v-else-if="assignments.error.value && !rows.length" class="ws-state-line"><p role="alert">{{ bindingError(assignments.error.value) }}</p><button type="button" class="secondary-button" @click="assignments.refetch()">重试</button></div>
    <p v-else-if="!rows.length" class="ws-state-line">还没有 OpenClaw 智能体。</p>
    <div v-else class="ws-table-wrap">
      <table class="ws-table asv-table mc-assign">
        <thead><tr><th scope="col">智能体</th><th scope="col">使用的连接</th><th scope="col">使用的模型</th><th scope="col">应用状态</th><th scope="col">操作</th></tr></thead>
        <tbody>
          <tr v-for="r in rows" :key="r.instance_id">
            <th scope="row" data-label="智能体"><span class="asv-name"><Bot aria-hidden="true" /><span>{{ r.instance_name }}</span></span></th>
            <td data-label="使用的连接">
              <template v-if="r.binding?.revoked">已解除（不使用模型）</template>
              <template v-else-if="target(r)">{{ target(r)!.connection_name ?? '（连接已删除）' }}</template>
              <template v-else>未设置</template>
              <span v-if="differs(r) && r.binding?.applied_revision !== null" class="asv-sub">已应用：{{ r.binding?.applied_connection_id ? r.binding?.applied_connection_name ?? '（连接已删除）' : '已移除受管模型' }}</span>
            </td>
            <td data-label="使用的模型">
              <code v-if="target(r)?.model_id">{{ target(r)!.model_id }}</code><template v-else>—</template>
              <span v-if="differs(r) && r.binding?.applied_model_id" class="asv-sub">已应用：<code>{{ r.binding.applied_model_id }}</code></span>
            </td>
            <td data-label="应用状态">
              <span class="asv-state" :class="`tone-${state(r).tone}`" :title="state(r).hint">{{ state(r).label }}</span>
              <span v-if="r.binding?.last_error_code" class="asv-sub">原因：{{ reasonText(r.binding.last_error_code) }}</span>
            </td>
            <td class="asv-link-cell"><button type="button" class="secondary-button mc-small" :disabled="busy" :aria-label="`更换连接：${r.instance_name}`" @click="open(r)">更换连接</button></td>
          </tr>
        </tbody>
      </table>
    </div>
    <footer v-if="rows.length" class="asv-foot ag-foot">
      <span>已显示 {{ rows.length }} 个<template v-if="typeof total === 'number'">，共 {{ total }} 个</template><template v-if="assignments.error.value"> · 刷新失败，显示的是上次取回的数据</template></span>
      <button v-if="assignments.hasNextPage.value" type="button" class="secondary-button" :disabled="assignments.isFetchingNextPage.value" @click="assignments.fetchNextPage()">加载更多</button>
    </footer>
    <p v-if="notice && !editing" class="feedback" role="status">{{ notice }}</p>

    <ModalPanel v-if="editing" :title="`更换连接：${editing.instance_name}`" :busy="busy" @close="close">
      <form class="mc-assign-form" aria-label="选择连接与模型" @submit.prevent="save">
        <p class="form-help">当前目标：<template v-if="target(editing)">{{ target(editing)!.connection_name ?? '（连接已删除）' }} · <code>{{ target(editing)!.model_id }}</code></template><template v-else>未设置</template>。{{ state(editing).hint }}</p>
        <ModelConnectionPicker v-model="choice" :instance-id="editing.instance_id" :disabled="busy" />
        <p v-if="error" class="feedback" role="alert">{{ error }}</p>
        <div class="form-actions"><button type="submit" class="primary-button" :disabled="busy || !choice || same">{{ busy ? '正在保存…' : '保存' }}</button><button type="button" class="secondary-button" :disabled="busy" @click="close">取消</button></div>
      </form>
    </ModalPanel>
  </ManagementSection>
</template>
