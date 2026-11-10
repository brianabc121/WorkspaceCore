<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, reactive, ref, useId, watch } from 'vue'
import { ArrowRight, Bot, Copy, Info, Lock, Power, TriangleAlert } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf, type Schema } from '../../services/api'
import { useAgentAction } from '../agents/action'
import { formatTime } from '../time'
import { PROTOCOL_LABEL, configStatus, isCode, mcError, modelsInUse, syncText, useAllInstances, useAllModels, useGrants, type Capabilities, type Connection, type ConnectionCheck, type CreatePrefill, type Protocol, type Provider, type SyncResult } from './api'
import ModelScopeEditor from './ModelScopeEditor.vue'
import CheckResult from './CheckResult.vue'

// 连接配置（规范第 4.2 节、契约第 2 节）：新建与编辑共用。
// - 官方直连只能从经核验的官方预设里选，协议与 URL 由预设填写；要用别的地址就切到“第三方中转”，不挂官方标识。
// - 协议只列 capabilities.supported_protocols（未核验的不出现）。
// - API Key 只在这个表单的内存里：新建时随创建一起提交，已保存的 Key 不回显，用独立的“更换”表单提交非空的新值；
//   提交、取消、关闭都清空。编辑名称 / 地址等用 PATCH（不含 Key），模型范围用 PUT models 整体替换。
// - 已有授权或绑定的连接改 URL / 协议返回 409 CONNECTION_ENDPOINT_IN_USE：提示“复制为新连接”（预填非敏感字段，Key 重新输入）。
// - 保存、检测、同步、更换 Key 各自独立的 busy / 错误。
const props = defineProps<{ connection: Connection | null; capabilities: Capabilities | undefined; prefill?: CreatePrefill | null; intent?: 'check' | null }>()
const emit = defineEmits<{ created: [id: string]; copy: [prefill: CreatePrefill]; grants: []; toggle: [connection: Connection]; cancel: [] }>()
const auth = useAuthStore(), scope = auth.workspaceId!
const ids = { name: useId(), description: useId(), type: useId(), preset: useId(), protocol: useId(), url: useId(), key: useId(), newKey: useId(), checkModel: useId(), draft: useId() }

const saving = useAgentAction(scope, mcError)
const checking = useAgentAction(scope, mcError)
const syncing = useAgentAction(scope, mcError)
const rotating = useAgentAction(scope, mcError)

const editing = computed(() => !!props.connection)
const presets = computed(() => props.capabilities?.official_presets ?? [])
const protocols = computed(() => props.capabilities?.supported_protocols ?? [])
const maxModels = computed(() => props.capabilities?.limits?.max_models ?? 1000)
const freshSeconds = computed(() => props.capabilities?.limits?.check_fresh_seconds ?? 15 * 60)

// ---- 表单（不含 Key） ----
type Meta = { name: string; description: string; connection_type: 'official' | 'relay' | null; official_provider: Provider | null; protocol: Protocol | ''; base_url: string }
const form = reactive<Omit<Meta, 'connection_type'> & { connection_type: 'official' | 'relay'; model_scope: 'selected' | 'all_available'; models: string[]; draft: boolean }>({ name: '', description: '', connection_type: 'relay', official_provider: null, protocol: '', base_url: '', model_scope: 'selected', models: [], draft: false })
const apiKey = ref('')
// 基准要是响应式的：保存成功后改基准，“有未保存的修改”立刻跟着变
let baseVersion = 0
const baseMeta = ref(''), baseModels = ref('')
const meta = (): Meta => ({ name: form.name.trim(), description: form.description.trim(), connection_type: form.connection_type, official_provider: form.connection_type === 'official' ? form.official_provider : null, protocol: form.protocol, base_url: form.base_url.trim() })
const modelsKey = () => JSON.stringify([form.model_scope, form.model_scope === 'selected' ? [...form.models].sort() : []])
const metaDirty = computed(() => JSON.stringify(meta()) !== baseMeta.value)
// 编辑时完整的模型集合读到之前不算改过（不会用空列表整体替换）
const modelsLoaded = ref(false)
const modelsDirty = computed(() => (props.connection ? modelsLoaded.value && modelsKey() !== baseModels.value : false))

const all = useAllModels(scope, () => props.connection?.id ?? null)
const discovered = computed(() => all.data.value?.discovered ?? [])
const savedModels = computed(() => all.data.value?.items ?? [])
const displayNames = computed(() => new Map(savedModels.value.map(m => [m.model_id, m.display_name])))

// 基准取服务端的原值（迁移草稿的类型、协议可能为空）：表单补上的默认值算作修改，保存时一并提交
const metaOf = (c: Connection): Meta => ({ name: c.name.trim(), description: c.description.trim(), connection_type: c.connection_type, official_provider: c.connection_type === 'official' ? c.official_provider : null, protocol: c.protocol ?? '', base_url: (c.base_url ?? '').trim() })
function initMeta(c: Connection | null) {
  const p = props.prefill
  baseVersion = c?.version ?? 0
  const type = c?.connection_type ?? p?.connection_type ?? 'relay'
  Object.assign(form, {
    name: c?.name ?? p?.name ?? '', description: c?.description ?? p?.description ?? '',
    connection_type: type, official_provider: c?.official_provider ?? p?.official_provider ?? null,
    protocol: c?.protocol ?? p?.protocol ?? (type === 'relay' && protocols.value.length === 1 ? protocols.value[0] : ''),
    base_url: c?.base_url ?? p?.base_url ?? '', model_scope: c?.model_scope ?? p?.model_scope ?? 'selected', draft: false,
  })
  if (type === 'official' && !form.official_provider) pickPreset(presets.value[0]?.provider ?? null)
  baseMeta.value = c ? JSON.stringify(metaOf(c)) : ''
}
function initModels(list: string[]) {
  form.models = [...list]
  baseModels.value = props.connection ? JSON.stringify([props.connection.model_scope, props.connection.model_scope === 'selected' ? [...list].sort() : []]) : ''
  if (props.connection) modelsLoaded.value = true
}
initMeta(props.connection)
if (!props.connection) initModels(props.prefill?.models ?? [])
let reload = false
// 后台刷新（别处改了版本）：没有未保存的修改就跟着最新的走；冲突之后无条件重新读取
watch(() => props.connection, (c, old) => {
  if (!c) return
  if (reload || !old || c.id !== old.id || (c.version !== old.version && !metaDirty.value && !modelsDirty.value)) {
    initMeta(c)
    if (all.data.value) initModels(c.model_scope === 'selected' ? all.data.value.items.map(m => m.model_id) : [])
    reload = false
  }
})
watch(() => all.data.value, data => {
  if (!data || !props.connection) return
  if (!modelsLoaded.value || !modelsDirty.value || reload) initModels(props.connection.model_scope === 'selected' ? data.items.map(m => m.model_id) : [])
}, { immediate: true })
// 新建：开放的协议只有一个时预选它（能力表可能比表单晚到）
watch(protocols, list => { if (!props.connection && form.connection_type === 'relay' && !form.protocol && list.length === 1) form.protocol = list[0]! })
watch(presets, list => { if (!props.connection && form.connection_type === 'official' && !form.official_provider) pickPreset(list[0]?.provider ?? null) })

// ---- 类型、官方预设、协议 ----
function pickPreset(provider: Provider | null) {
  const preset = presets.value.find(p => p.provider === provider)
  form.official_provider = preset?.provider ?? null
  if (preset) { form.protocol = preset.protocol; form.base_url = preset.base_url }
}
function setType(type: 'official' | 'relay') {
  if (type === form.connection_type) return
  form.connection_type = type
  if (type === 'official') pickPreset(form.official_provider ?? presets.value[0]?.provider ?? null)
  else form.official_provider = null
}
const preset = computed(() => presets.value.find(p => p.provider === form.official_provider) ?? null)
const protocolOptions = computed(() => {
  const list: { value: Protocol; label: string; disabled: boolean }[] = protocols.value.map(p => ({ value: p, label: PROTOCOL_LABEL[p], disabled: false }))
  if (form.protocol && !protocols.value.includes(form.protocol)) list.push({ value: form.protocol, label: `${PROTOCOL_LABEL[form.protocol]}（未开放）`, disabled: true })
  return list
})

// ---- 校验 ----
const urlProblem = computed(() => {
  const value = form.base_url.trim()
  if (!value) return ''
  let url: URL
  try { url = new URL(value) } catch { return '请输入完整的地址，例如 https://api.example.com/v1' }
  if (url.protocol !== 'https:' && url.protocol !== 'http:') return '只接受 HTTPS 地址。'
  if (url.username || url.password) return '地址里不能带用户名或密码（Key 填在下面的 API Key 里）。'
  if (url.search || url.hash) return '地址里不能带查询参数或 # 片段；只填协议基地址。'
  return ''
})
const urlNote = computed(() => (/^http:\/\//i.test(form.base_url.trim()) ? '这是明文 HTTP 地址：只有部署允许时才能保存。' : ''))
const draftAllowed = computed(() => !editing.value && (form.draft || form.model_scope === 'all_available'))
const createProblem = computed(() => {
  if (!form.name.trim()) return '请填写连接名称。'
  if (!apiKey.value) return '请填写 API Key。'
  if (urlProblem.value) return urlProblem.value
  if (draftAllowed.value) return ''
  if (form.connection_type === 'official' && !preset.value) return '请选择一个官方预设。'
  if (!form.protocol) return '请选择接口协议。'
  if (!form.base_url.trim()) return '请填写连接 URL。'
  if (form.model_scope === 'selected' && !form.models.length) return '请至少添加一个模型 ID，或勾选“保存为草稿”。'
  return ''
})
const editProblem = computed(() => {
  if (!form.name.trim()) return '请填写连接名称。'
  if (urlProblem.value) return urlProblem.value
  if (form.model_scope === 'selected' && modelsDirty.value && !form.models.length) return '“指定模型”至少要有一个模型 ID。'
  return ''
})
const dirty = computed(() => metaDirty.value || modelsDirty.value)

// ---- 保存 ----
const endpointBlocked = ref(false)
const inUse = ref<{ model: string; names: string[] }[]>([])
let idempotency: string | null = null
const newKey = () => (typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`)
const instances = useAllInstances(scope, () => inUse.value.length > 0)
const instanceName = (id: string) => instances.data.value?.find(i => i.id === id)?.name ?? id

async function create() {
  if (createProblem.value || saving.busy.value) return
  const draft = draftAllowed.value
  const body: Schema['ModelConnectionCreate'] = {
    name: form.name.trim(), description: form.description.trim(), connection_type: form.connection_type,
    official_provider: form.connection_type === 'official' ? form.official_provider : null,
    protocol: form.protocol || null, base_url: form.base_url.trim() || null, api_key: apiKey.value,
    model_scope: form.model_scope, models: form.model_scope === 'selected' ? form.models.map(model_id => ({ model_id })) : [], save_as_draft: draft,
  }
  apiKey.value = ''
  // 结果未知后重试沿用同一个幂等键：服务端返回原来那条连接，不会再建一份凭据
  idempotency ??= newKey()
  let created: Connection | null = null
  const ok = await saving.act(async () => {
    created = dataOf(await auth.api.POST('/api/v1/model-connections', { params: { header: { ...saving.header, 'Idempotency-Key': idempotency } }, body }))
  }, draft ? '已保存为草稿（停用）：补全配置后再启用。' : '连接已保存并启用。')
  if (!ok && isCode(saving.failure.value, 'WRITE_UNKNOWN')) return
  idempotency = null
  if (ok && created) emit('created', (created as Connection).id)
}

async function save() {
  const c = props.connection
  if (!c || editProblem.value || !dirty.value || saving.busy.value) return
  endpointBlocked.value = false; inUse.value = []
  const before = JSON.parse(baseMeta.value) as Meta, now = meta()
  const patch: Schema['ModelConnectionUpdate'] = { version: baseVersion, confirm_impact: false }
  if (now.name !== before.name) patch.name = now.name
  if (now.description !== before.description) patch.description = now.description
  if (now.connection_type !== before.connection_type) patch.connection_type = now.connection_type
  if (now.official_provider !== before.official_provider) patch.official_provider = now.official_provider
  if (now.protocol !== before.protocol) patch.protocol = now.protocol || null
  if (now.base_url !== before.base_url) patch.base_url = now.base_url || null
  let step = 'meta' as 'meta' | 'models', metaSaved = false as boolean
  const ok = await saving.act(async () => {
    // 每一步成功后立刻把它当成新的基准（之后的刷新不会把表单当成“有未保存的修改”）
    if (metaDirty.value) {
      baseVersion = dataOf(await auth.api.PATCH('/api/v1/model-connections/{connection_id}', { params: { header: saving.header, path: { connection_id: c.id } }, body: patch })).version
      baseMeta.value = JSON.stringify(meta())
      metaSaved = true
    }
    step = 'models'
    if (modelsDirty.value) {
      const models = form.model_scope === 'selected' ? form.models.map(model_id => ({ model_id, display_name: displayNames.value.get(model_id) || null })) : []
      baseVersion = dataOf(await auth.api.PUT('/api/v1/model-connections/{connection_id}/models', { params: { header: saving.header, path: { connection_id: c.id } }, body: { version: baseVersion, model_scope: form.model_scope, models } })).version
      baseModels.value = modelsKey()
    }
  }, '配置已保存。')
  if (ok) return
  const failure = saving.failure.value
  if (step === 'models' && metaSaved) saving.error.value = `连接信息已保存，模型范围没有保存：${saving.error.value}`
  if (isCode(failure, 'CONNECTION_ENDPOINT_IN_USE')) endpointBlocked.value = true
  if (isCode(failure, 'MODEL_IN_USE')) inUse.value = Object.entries(modelsInUse(failure)).map(([model, list]) => ({ model, names: list }))
  // 版本冲突：重新读取最新的连接并按它重置表单
  if (isCode(failure, 'VERSION_CONFLICT')) { reload = true; void saving.refresh() }
}
function revertEndpoint() {
  const before = JSON.parse(baseMeta.value) as Meta
  Object.assign(form, { connection_type: before.connection_type ?? 'relay', official_provider: before.official_provider, protocol: before.protocol, base_url: before.base_url })
  endpointBlocked.value = false
  saving.error.value = ''
}
function copyAsNew() {
  emit('copy', { name: `${form.name.trim()} 副本`.slice(0, 64), description: form.description.trim(), connection_type: form.connection_type, official_provider: form.connection_type === 'official' ? form.official_provider : null, protocol: form.protocol || null, base_url: form.base_url.trim() || null, model_scope: form.model_scope, models: form.model_scope === 'selected' ? [...form.models] : [] })
}

// ---- 更换 Key：独立的表单，只提交非空的新值 ----
const replacing = ref(false), replacement = ref('')
const replaceRef = ref<HTMLInputElement | null>(null)
async function openReplace() { replacing.value = true; replacement.value = ''; rotating.error.value = ''; rotating.notice.value = ''; await nextTick(); replaceRef.value?.focus() }
function closeReplace() { replacing.value = false; replacement.value = '' }
async function rotate() {
  const c = props.connection
  const value = replacement.value
  if (!c || !value || rotating.busy.value) return
  replacement.value = ''
  let out: Connection | null = null
  const ok = await rotating.act(async () => { out = dataOf(await auth.api.POST('/api/v1/model-connections/{connection_id}/rotate-key', { params: { header: rotating.header, path: { connection_id: c.id } }, body: { version: c.version, api_key: value } })) }, 'Key 已更换：旧值不再下发，以它为主模型的智能体会重新应用；之前的检测结果已失效。')
  replacing.value = false
  if (ok && out && baseVersion === c.version) baseVersion = (out as Connection).version
}

// ---- 同步模型 ----
const syncResult = ref<SyncResult | null>(null)
async function sync() {
  const c = props.connection
  if (!c || syncing.busy.value) return
  syncResult.value = null
  let out: SyncResult | null = null
  await syncing.act(async () => { out = dataOf(await auth.api.POST('/api/v1/model-connections/{connection_id}/models/sync', { params: { header: syncing.header, path: { connection_id: c.id } }, body: { version: c.version } })) })
  if (out) {
    const result: SyncResult = out
    syncResult.value = result
    if (baseVersion === c.version) baseVersion = result.connection.version
  }
}
const syncShown = computed(() => (syncResult.value ? syncText(syncResult.value) : null))

// ---- 检测：元数据或推理（推理要选模型，可能产生少量费用） ----
const checkOpen = ref(props.intent === 'check'), checkMode = ref<'metadata' | 'inference'>('metadata'), checkModel = ref('')
const checkResult = ref<ConnectionCheck | null>(null)
const shownCheck = computed(() => checkResult.value ?? props.connection?.last_check ?? null)
const checkable = computed(() => !!props.connection?.protocol && !!props.connection?.base_url && props.connection.migration_state !== 'non_model')
async function check() {
  const c = props.connection
  if (!c || checking.busy.value || (checkMode.value === 'inference' && !checkModel.value)) return
  let out: ConnectionCheck | null = null
  await checking.act(async () => { out = dataOf(await auth.api.POST('/api/v1/model-connections/{connection_id}/checks', { params: { header: checking.header, path: { connection_id: c.id } }, body: { version: c.version, mode: checkMode.value, ...(checkMode.value === 'inference' ? { model_id: checkModel.value } : {}) } })) })
  if (out) checkResult.value = out
}
function toggleCheck() { checkOpen.value = !checkOpen.value; checking.error.value = '' }

// ---- 关联智能体 ----
const grants = useGrants(scope, () => props.connection?.id ?? null)
const status = computed(() => (props.connection ? configStatus(props.connection) : null))

// Key 不留在内存里
onBeforeUnmount(() => { apiKey.value = ''; replacement.value = '' })
function cancel() { apiKey.value = ''; emit('cancel') }
const submit = () => (editing.value ? save() : create())
const describe = (error: unknown) => (error instanceof ApiError ? mcError(error) : '请求失败，请稍后重试。')
</script>

<template>
  <form class="mc-form" :aria-label="editing ? '连接配置' : '添加连接'" autocomplete="off" novalidate @submit.prevent="submit">
    <p v-if="connection && status" class="mc-form-status">
      <span class="asv-state" :class="`tone-${status.tone}`">{{ status.label }}</span>
      <span class="asv-sub">配置完整度、启停与检测结果分开显示；检测失败不会自动停用。</span>
      <button v-if="connection.migration_state !== 'non_model'" type="button" class="secondary-button mc-inline" :disabled="saving.busy.value" @click="emit('toggle', connection)"><Power aria-hidden="true" />{{ connection.enabled ? '停用' : '启用' }}</button>
    </p>

    <div class="mc-row">
      <label :for="ids.name">连接名称</label>
      <input :id="ids.name" v-model="form.name" required maxlength="64" :autofocus="!editing" />
    </div>
    <div class="mc-row">
      <label :for="ids.description">说明</label>
      <input :id="ids.description" v-model="form.description" maxlength="500" placeholder="选填" />
    </div>
    <div class="mc-row">
      <span :id="ids.type" class="mc-label">连接类型</span>
      <div class="mc-segment" role="radiogroup" :aria-labelledby="ids.type">
        <label :class="{ 'is-on': form.connection_type === 'official', 'is-off': !presets.length }"><input type="radio" :name="ids.type" value="official" :checked="form.connection_type === 'official'" :disabled="!presets.length" @change="setType('official')" />官方直连</label>
        <label :class="{ 'is-on': form.connection_type === 'relay' }"><input type="radio" :name="ids.type" value="relay" :checked="form.connection_type === 'relay'" @change="setType('relay')" />第三方中转</label>
      </div>
    </div>
    <p v-if="!presets.length" class="form-help mc-indent">暂无经核验的官方预设，只能添加第三方中转。</p>
    <div v-if="form.connection_type === 'official'" class="mc-row">
      <label :for="ids.preset">官方预设</label>
      <select :id="ids.preset" :value="form.official_provider ?? ''" @change="pickPreset((($event.target as HTMLSelectElement).value || null) as Provider | null)">
        <option value="" disabled>选择官方服务商</option>
        <option v-for="p in presets" :key="p.provider" :value="p.provider">{{ p.label }}</option>
      </select>
    </div>
    <div class="mc-row">
      <label :for="ids.protocol">接口协议</label>
      <select :id="ids.protocol" v-model="form.protocol" :disabled="form.connection_type === 'official'">
        <option value="" disabled>{{ protocols.length ? '选择协议' : '没有开放的协议' }}</option>
        <option v-for="p in protocolOptions" :key="p.value" :value="p.value" :disabled="p.disabled">{{ p.label }}</option>
      </select>
    </div>
    <div class="mc-row">
      <label :for="ids.url">连接 URL</label>
      <input :id="ids.url" v-model="form.base_url" type="url" inputmode="url" spellcheck="false" placeholder="https://gateway.example.com/v1" :readonly="form.connection_type === 'official'" :aria-invalid="!!urlProblem" maxlength="2048" />
    </div>
    <p v-if="urlProblem" class="feedback mc-indent" role="alert">{{ urlProblem }}</p>
    <p v-else-if="form.connection_type === 'official'" class="form-help mc-indent">官方地址由预设填写<template v-if="preset">（核验来源：<a :href="preset.source" target="_blank" rel="noopener noreferrer">官方文档</a>）</template>；要用别的地址请切换为“第三方中转”。</p>
    <p v-else class="form-help mc-indent">填协议基地址（例如以 /v1 结尾），不是 /chat/completions 这样的单次请求地址。{{ urlNote }}</p>

    <div class="mc-row">
      <label :for="ids.key">API Key</label>
      <div v-if="!editing" class="mc-key"><input :id="ids.key" v-model="apiKey" type="password" autocomplete="new-password" spellcheck="false" maxlength="8192" required /></div>
      <div v-else class="mc-key">
        <p :id="ids.key" class="mc-saved"><Lock aria-hidden="true" />{{ connection?.credential_configured ? '已加密保存' : '没有可用的 Key' }}</p>
        <button type="button" class="link-button" :disabled="rotating.busy.value || connection?.migration_state === 'non_model'" :aria-expanded="replacing" @click="replacing ? closeReplace() : openReplace()">{{ connection?.credential_configured ? '更换' : '填写' }}</button>
      </div>
    </div>
    <p class="form-help mc-indent">{{ editing ? (connection?.credential_configured ? '已加密保存，原值不回显、不能复制或导出。' : '保存的 Key 无法解密，请填写新的 Key。') : '保存后不再显示，只能更换。' }}</p>
    <div v-if="replacing" class="mc-subform" role="group" aria-label="更换 API Key">
      <label :for="ids.newKey" class="mc-label">新的 API Key</label>
      <input :id="ids.newKey" ref="replaceRef" v-model="replacement" type="password" autocomplete="new-password" spellcheck="false" maxlength="8192" @keydown.enter.prevent="rotate" />
      <p class="form-help">提交后旧值不再下发，绑定它的智能体会重新应用；不会去服务商撤销旧 Key。</p>
      <div class="form-actions"><button type="button" class="primary-button" :disabled="rotating.busy.value || !replacement" @click="rotate">{{ rotating.busy.value ? '正在更换…' : '确认更换' }}</button><button type="button" class="secondary-button" :disabled="rotating.busy.value" @click="closeReplace">取消</button></div>
    </div>
    <p v-if="rotating.error.value" class="feedback" role="alert">{{ rotating.error.value }}</p>
    <p v-if="rotating.notice.value" class="feedback" role="status">{{ rotating.notice.value }}</p>

    <hr class="mc-divider" />
    <ModelScopeEditor
      :scope="form.model_scope" :models="form.models" :discovered="discovered" :can-sync="editing && !!connection?.protocol && !!connection?.base_url" :syncing="syncing.busy.value" :max-models="maxModels" :disabled="editing && !modelsLoaded"
      @update:scope="form.model_scope = $event" @update:models="form.models = $event" @sync="sync"
    />
    <p v-if="editing && form.model_scope === 'all_available' && !discovered.length && connection?.model_scope !== 'all_available'" class="form-help">没有完整同步过的模型列表时不能保存为“服务商全部可用模型”：先同步模型。</p>
    <p v-if="all.isError.value" class="feedback" role="alert">模型列表读取失败：{{ describe(all.error.value) }}</p>
    <p v-if="syncing.error.value" class="feedback" role="alert">{{ syncing.error.value }}</p>
    <p v-if="syncShown" class="mc-note" :class="`tone-${syncShown.tone}`" role="status">{{ syncShown.text }}<template v-if="syncResult?.retry_at"> 可在 <time :datetime="syncResult.retry_at">{{ formatTime(syncResult.retry_at) }}</time> 之后重试。</template></p>
    <label v-if="!editing && form.model_scope === 'selected'" class="mc-check-line"><input :id="ids.draft" v-model="form.draft" type="checkbox" />保存为草稿（暂不启用；可以先不填 URL、协议或模型）</label>

    <template v-if="editing">
      <CheckResult v-if="connection?.last_check || checkResult" :check="shownCheck" :fresh-seconds="freshSeconds" />
      <div v-if="checkOpen" class="mc-subform" role="group" aria-label="检测连接">
        <div class="mc-radios" role="radiogroup" aria-label="检测方式">
          <label class="mc-radio"><input v-model="checkMode" type="radio" value="metadata" />元数据检测<span class="asv-sub">请求模型列表接口，只证明 Key 与接口可用</span></label>
          <label class="mc-radio"><input v-model="checkMode" type="radio" value="inference" />推理检测<span class="asv-sub">对一个模型发一次最短的真实请求</span></label>
        </div>
        <template v-if="checkMode === 'inference'">
          <p class="mc-warn" role="note"><TriangleAlert aria-hidden="true" />推理检测会向服务商发一次真实请求，可能产生少量服务商费用。</p>
          <label :for="ids.checkModel" class="mc-label">检测的模型</label>
          <select :id="ids.checkModel" v-model="checkModel">
            <option value="" disabled>{{ savedModels.length ? '选择一个模型' : '还没有保存的模型' }}</option>
            <option v-for="m in savedModels" :key="m.id" :value="m.model_id">{{ m.model_id }}</option>
          </select>
        </template>
        <div class="form-actions"><button type="button" class="primary-button" :disabled="checking.busy.value || (checkMode === 'inference' && !checkModel)" @click="check">{{ checking.busy.value ? '正在检测…' : '开始检测' }}</button><button type="button" class="secondary-button" :disabled="checking.busy.value" @click="toggleCheck">收起</button></div>
      </div>
      <p v-if="!checkable" class="form-help">填好协议与 URL 并保存后才能检测。</p>
      <p v-if="checking.error.value" class="feedback" role="alert">{{ checking.error.value }}</p>

      <section class="mc-agents" aria-label="关联智能体">
        <h3>关联智能体</h3>
        <div class="mc-agents-box">
          <ul v-if="grants.data.value?.length" class="mc-agent-chips">
            <li v-for="g in grants.data.value" :key="g.instance_id"><Bot aria-hidden="true" />{{ g.instance_name }}<span v-if="g.bound" class="asv-sub">主模型</span></li>
          </ul>
          <p v-else-if="grants.isPending.value" class="form-help" role="status">正在读取授权…</p>
          <p v-else class="form-help">还没有授权给智能体。</p>
          <button type="button" class="link-button mc-link" @click="emit('grants')">管理授权<ArrowRight aria-hidden="true" /></button>
        </div>
      </section>
    </template>

    <div v-if="endpointBlocked" class="mc-callout" role="alert">
      <p><strong><Info aria-hidden="true" />不能直接修改地址或协议</strong></p>
      <p>这个连接已授权给智能体或正被使用，改地址会把现有 Key 发到新的目标。请复制为新连接（Key 需要重新输入），再在“OpenClaw 连接分配”里把智能体切换过去。</p>
      <div class="form-actions"><button type="button" class="primary-button mc-inline" @click="copyAsNew"><Copy aria-hidden="true" />复制为新连接</button><button type="button" class="secondary-button" @click="revertEndpoint">撤销地址修改</button></div>
    </div>
    <div v-if="inUse.length" class="mc-callout" role="note">
      <p><strong>这些模型正被智能体使用</strong></p>
      <ul><li v-for="u in inUse" :key="u.model"><code>{{ u.model }}</code>：{{ u.names.map(instanceName).join('、') }}</li></ul>
      <p>请先在“OpenClaw 连接分配”里给它们换模型，再移除。</p>
    </div>
    <p v-if="saving.error.value && !endpointBlocked" class="feedback" role="alert">{{ saving.error.value }}</p>
    <p v-if="saving.notice.value" class="feedback" role="status">{{ saving.notice.value }}</p>
    <p v-if="!editing && createProblem && (form.name || apiKey)" class="form-help">{{ createProblem }}</p>

    <div class="mc-form-actions">
      <button type="submit" class="primary-button" :disabled="saving.busy.value || (editing ? !!editProblem || !dirty : !!createProblem)">{{ saving.busy.value ? '正在保存…' : editing ? '保存配置' : draftAllowed ? '保存为草稿' : '保存配置' }}</button>
      <button v-if="editing" type="button" class="secondary-button" :disabled="checking.busy.value || !checkable" :aria-expanded="checkOpen" @click="toggleCheck">检测连接</button>
      <button v-else type="button" class="secondary-button" :disabled="saving.busy.value" @click="cancel">取消</button>
    </div>
  </form>
</template>
