<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf, isAccessError, type Schema } from '../../services/api'
import { useAutomationAccess } from './queries'
import { automationError, triggerParams } from './validation'
import ModalPanel from '../../components/ModalPanel.vue'
const props = defineProps<{ automation: Schema['AutomationOut']; permitted: boolean }>()
const emit = defineEmits<{ close: []; records: [] }>(), auth = useAuthStore(), cache = useQueryClient(), access = useAutomationAccess()
const scope = auth.workspaceId!, user = auth.user?.id, epoch = access.epoch
const active = () => auth.user?.id === user && auth.workspaceId === scope && epoch === access.epoch
const marker = 'workspace:trigger:' + user + ':' + scope + ':' + props.automation.id
function remembered() { try { return !!sessionStorage.getItem(marker) } catch { return false } }
function remember(key?: string) { try { if (key) sessionStorage.setItem(marker, key); else sessionStorage.removeItem(marker) } catch { /* In-memory state still protects this form. */ } }
const inputs = reactive<Record<string, string>>({}), phase = ref<'editing' | 'confirm' | 'sending' | 'unknown' | 'accepted'>(remembered() ? 'unknown' : 'editing')
const restored = ref(remembered()), error = ref(''), result = ref<Schema['AutomationRunOut'] | null>(null), checked = ref(false)
let request: { key: string; params: Schema['TriggerRequest']['params'] } | null = null
const mayWrite = computed(() => active() && props.permitted && !access.denied && auth.currentWorkspace?.role !== 'viewer')
function prepare() {
  if (!mayWrite.value) return
  error.value = ''
  try { request = { key: crypto.randomUUID(), params: triggerParams(props.automation.params, inputs) }; phase.value = 'confirm' }
  catch (cause) { error.value = (cause as Error).message }
}
async function send() {
  if (!request || !mayWrite.value || phase.value === 'sending') return
  phase.value = 'sending'; error.value = ''; remember(request.key)
  try {
    const run = dataOf(await auth.api.POST('/api/v1/automations/{automation_id}/trigger', { params: { header: { 'X-Workspace-Id': scope, 'Idempotency-Key': request.key }, path: { automation_id: props.automation.id } }, body: { params: request.params } }))
    if (!active()) return
    result.value = run; phase.value = run.status === 'unknown' ? 'unknown' : 'accepted'
    if (run.status !== 'unknown') remember()
    request = null; for (const key of Object.keys(inputs)) delete inputs[key]
    void cache.invalidateQueries({ predicate: q => ['automations', 'automation-runs', 'notifications', 'overview'].includes(String(q.queryKey[0])) && q.queryKey[1] === user && q.queryKey[2] === scope })
  } catch (cause) {
    if (!active()) return
    if (isAccessError(cause)) access.denied = true
    const unknown = !(cause instanceof ApiError) || cause.status === 0 || cause.status >= 500 || cause.code === 'IDEMPOTENCY_IN_PROGRESS'
    phase.value = unknown ? 'unknown' : 'editing'; error.value = automationError(cause)
    if (!unknown) { remember(); request = null }
  }
}
function records() { checked.value = true; emit('records') }
function fresh() {
  if (!checked.value || !mayWrite.value || !window.confirm('已核对运行记录。新的触发可能重复执行，仍要开始新提交吗？')) return
  remember(); restored.value = false; result.value = null; request = null; phase.value = 'editing'; error.value = ''
}
</script>
<template>
  <ModalPanel :title="'触发工作流：' + automation.name" :busy="phase === 'sending'" @close="phase !== 'sending' && $emit('close')">
    <p v-if="!mayWrite" role="alert">当前无权触发，或连接状态已变化，请刷新后确认。</p>
    <form v-if="phase === 'editing'" novalidate @submit.prevent="prepare">
      <p class="form-help">只提交此白名单声明的参数；触发前还需确认。敏感参数不会写入运行记录。</p>
      <label v-for="spec in automation.params" :key="spec.name" class="form-field">{{ spec.label }}{{ spec.required ? ' *' : '' }}
        <select v-if="spec.type === 'boolean' || spec.choices" v-model="inputs[spec.name]" :aria-label="spec.label + (spec.required ? ' *' : '')" :disabled="!mayWrite"><option value="">请选择</option><template v-if="spec.type === 'boolean'"><option value="true">是</option><option value="false">否</option></template><option v-for="choice in spec.choices ?? []" v-else :key="choice" :value="choice">{{ choice }}</option></select>
        <input v-else v-model="inputs[spec.name]" :aria-label="spec.label + (spec.required ? ' *' : '')" :type="spec.sensitive ? 'password' : 'text'" :inputmode="spec.type === 'number' ? 'decimal' : undefined" autocomplete="off" :disabled="!mayWrite">
      </label><button class="primary-button" :disabled="!mayWrite" type="submit">检查并确认</button>
    </form>
    <div v-else-if="phase === 'confirm'"><p>确认触发「{{ automation.name }}」？这会执行已登记的工作流。</p><p v-for="spec in automation.params" :key="spec.name" class="form-help">{{ spec.label }}：{{ spec.sensitive ? '已填写（敏感值隐藏）' : inputs[spec.name] || '未填写' }}</p><div class="form-actions"><button class="secondary-button" @click="phase = 'editing'">返回修改</button><button class="primary-button" :disabled="!mayWrite" @click="send">确认触发</button></div></div>
    <p v-else-if="phase === 'sending'" role="status">正在提交，请勿重复操作…</p>
    <p v-else-if="phase === 'accepted'" role="status">已受理，等待执行。</p>
    <div v-else><p role="status">结果未知，正在确认</p><p>{{ restored ? '上次触发尚未确认，请先核对运行记录；未保存任何参数值。' : '不会自动重发。请核对运行记录，等待后台对账。' }}</p><div class="form-actions"><button class="secondary-button" @click="records">核对运行记录</button><button v-if="request" class="secondary-button" :disabled="!mayWrite" @click="send">用原请求核对</button><button v-if="checked" class="secondary-button" :disabled="!mayWrite" @click="fresh">已核对，开始新提交</button></div></div>
    <p v-if="error" role="alert">{{ error }}</p>
  </ModalPanel>
</template>
