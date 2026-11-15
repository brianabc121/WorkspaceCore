<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ArrowLeft, Plus, X } from '@lucide/vue'
import { ApiError } from '../../../services/api'
import { MODES, SANDBOX, bridgeError, platformName, type Bridge, type LlmBridgeApi, type Platform } from '../api'
import { GROUP_TARGETS, chatError, newRequestId, platformChoices, type ChatTarget, type LlmChatApi } from './chatApi'

// 新建一组同问：挑几个“管理器 + 平台 + 模型”（可以跨管理器），写一个问题，同时发给它们。
// 打开时先把当前管理器上已登录、能对话的平台都加进来，省得一个个添加。
const props = defineProps<{ chat: LlmChatApi; api: LlmBridgeApi; bridges: Bridge[]; managerId: string; narrow: boolean }>()
const emit = defineEmits<{ created: [groupId: string]; cancel: [] }>()

interface Chosen extends ChatTarget { key: number; label: string }
const targets = ref<Chosen[]>([])
let nextKey = 1, touched = false
const nameOf = (id: string) => props.bridges.find(b => b.id === id)?.name ?? '已删除的管理器'
const label = (t: ChatTarget) => `${platformName(t.platform)} · ${t.mode === 'dev' ? '开发 · ' : ''}${t.model || '默认模型'} · ${nameOf(t.bridge_id)}`
const same = (a: ChatTarget, b: ChatTarget) => a.bridge_id === b.bridge_id && a.platform === b.platform && a.mode === b.mode && (a.model ?? '') === (b.model ?? '') && (a.environment ?? '') === (b.environment ?? '')

// ---- 添加一个模型 ----
const manager = ref(props.managerId)
const status = props.api.useStatus(manager)
const choices = computed(() => platformChoices(status.data.value))
const usable = computed(() => choices.value.filter(c => c.loggedIn))
const platform = ref<Platform | null>(null), mode = ref<'chat' | 'dev'>('chat'), model = ref(''), effort = ref(''), environment = ref('')
const modes = computed(() => choices.value.find(c => c.id === platform.value)?.modes ?? [])
watch(usable, list => { if (!list.some(c => c.id === platform.value)) platform.value = (list[0]?.id as Platform | undefined) ?? null }, { immediate: true })
watch(modes, list => { if (!list.some(m => m.id === mode.value && m.available)) mode.value = list.find(m => m.available)?.id ?? list[0]?.id ?? 'chat' }, { immediate: true })
const options = props.chat.useOptions(manager, platform, mode)
const models = computed(() => options.data.value?.models ?? [])
const environments = computed(() => (options.data.value?.environments ?? []).filter(e => e.enabled))
const efforts = computed(() => (models.value.find(m => m.id === model.value) ?? models.value.find(m => m.default))?.efforts ?? [])
watch([manager, platform, mode], () => { model.value = ''; effort.value = ''; environment.value = '' })
watch(environments, list => { if (!list.some(e => e.id === environment.value)) environment.value = list.length === 1 ? list[0]!.id : '' })
watch(efforts, list => { if (!list.includes(effort.value)) effort.value = '' })
const sandbox = (id: string) => (SANDBOX as Record<string, string>)[id] ?? id
const statusProblem = computed(() => {
  if (status.error.value) return bridgeError(status.error.value)
  const data = status.data.value
  return data && !data.reachable ? `这个 MCP 管理器连不上${data.error ? `：${data.error}` : '。'}` : ''
})

// 当前管理器上已登录、能对话的平台：第一次读到状态时自动加进来
watch(() => status.data.value, data => {
  if (!data || touched || targets.value.length || manager.value !== props.managerId) return
  const enabled = props.bridges.find(b => b.id === props.managerId)?.enabled
  if (!enabled) return
  for (const c of platformChoices(data)) {
    if (targets.value.length >= 4 || !c.loggedIn || !c.modes.some(m => m.id === 'chat' && m.available)) continue
    const t: ChatTarget = { bridge_id: props.managerId, platform: c.id, mode: 'chat' }
    targets.value.push({ ...t, key: nextKey++, label: label(t) })
  }
}, { immediate: true })

const pickError = ref('')
function add() {
  pickError.value = ''
  const bridge = props.bridges.find(b => b.id === manager.value)
  if (!bridge?.enabled) { pickError.value = '这个 MCP 管理器已停用，不能加进来。'; return }
  if (!platform.value || !usable.value.some(c => c.id === platform.value)) { pickError.value = usable.value.length ? '选一个平台。' : '这个 MCP 管理器上还没有登录任何平台。'; return }
  if (!modes.value.find(m => m.id === mode.value)?.available) { pickError.value = '这个平台现在不能用这种用法。'; return }
  if (mode.value === 'dev' && !environment.value) { pickError.value = environments.value.length ? '开发会话要选一个开发环境。' : '这个平台还没有可用的开发环境。'; return }
  if (targets.value.length >= GROUP_TARGETS.max) { pickError.value = `最多 ${GROUP_TARGETS.max} 个。`; return }
  const t: ChatTarget = {
    bridge_id: manager.value, platform: platform.value, mode: mode.value,
    ...(model.value ? { model: model.value } : {}), ...(effort.value ? { effort: effort.value } : {}),
    ...(mode.value === 'dev' ? { environment: environment.value } : {}),
  }
  if (targets.value.some(x => same(x, t))) { pickError.value = '这个模型已经在里面了。'; return }
  touched = true
  targets.value.push({ ...t, key: nextKey++, label: label(t) })
}
function remove(key: number) { touched = true; targets.value = targets.value.filter(t => t.key !== key) }

// ---- 发出 ----
const text = ref(''), busy = ref(false), error = ref('')
let pending: { key: string; id: string } | null = null
async function submit() {
  if (busy.value) return
  const question = text.value.trim()
  if (targets.value.length < GROUP_TARGETS.min) { error.value = `至少选 ${GROUP_TARGETS.min} 个模型：只问一个的话用“新建会话”。`; return }
  if (!question) { error.value = '写下要问的问题。'; return }
  const list = targets.value.map(t => ({ bridge_id: t.bridge_id, platform: t.platform, mode: t.mode, ...(t.model ? { model: t.model } : {}), ...(t.effort ? { effort: t.effort } : {}), ...(t.environment ? { environment: t.environment } : {}) }))
  const key = JSON.stringify([list, question])
  if (pending?.key !== key) pending = { key, id: newRequestId() }
  busy.value = true; error.value = ''
  try {
    const out = await props.chat.createGroup({ title: '', text: question, request_id: pending.id, targets: list })
    pending = null
    emit('created', out.group.id)
  } catch (e) {
    const unknown = e instanceof ApiError && ['WRITE_UNKNOWN', 'EXTERNAL_TIMEOUT'].includes(e.code)
    error.value = unknown ? '连接中断或超时，不确定有没有建好：稍等一下看左边“同问”里有没有出现；没有的话再点一次（同一个问题不会建出两组）。' : chatError(e)
  } finally { busy.value = false }
}
</script>

<template>
  <form class="lc-new-form" novalidate aria-label="多模型同问" @submit.prevent="submit">
    <header class="lc-head">
      <button v-if="narrow" type="button" class="lc-icon" aria-label="返回会话列表" @click="emit('cancel')"><ArrowLeft aria-hidden="true" /></button>
      <div class="lc-head-main"><h2>多模型同问</h2><p class="lc-head-meta">同一个问题同时发给几个模型，并排看回答。每个模型各开一个会话，之后可以单独继续；可以跨 MCP 管理器。</p></div>
    </header>
    <div class="lc-new-body">
      <div class="form-field">
        <span id="lc-targets-label" class="lc-field-label">发给（{{ targets.length }} 个）</span>
        <ul v-if="targets.length" class="lc-targets" aria-labelledby="lc-targets-label">
          <li v-for="t in targets" :key="t.key" class="lc-target">{{ t.label }}<button type="button" class="lc-icon" :aria-label="`移除 ${t.label}`" @click="remove(t.key)"><X aria-hidden="true" /></button></li>
        </ul>
        <p v-else class="form-help">还没有选模型：在下面一个个添加。</p>
      </div>

      <fieldset class="lc-picker" aria-label="添加一个模型">
        <legend>添加一个模型</legend>
        <div class="form-field">
          <label for="lc-pick-manager">MCP 管理器</label>
          <select id="lc-pick-manager" v-model="manager"><option v-for="b in bridges" :key="b.id" :value="b.id">{{ b.name }}{{ b.enabled ? '' : '（已停用）' }}</option></select>
        </div>
        <div class="form-field">
          <label for="lc-pick-platform">平台</label>
          <select id="lc-pick-platform" v-model="platform" :disabled="!usable.length">
            <option v-if="!usable.length" :value="null">{{ status.isPending.value ? '正在读取…' : '没有已登录的平台' }}</option>
            <option v-for="c in usable" :key="c.id" :value="c.id">{{ platformName(c.id) }}</option>
          </select>
        </div>
        <div v-if="modes.length > 1" class="form-field">
          <label for="lc-pick-mode">用法</label>
          <select id="lc-pick-mode" v-model="mode"><option v-for="m in modes" :key="m.id" :value="m.id" :disabled="!m.available">{{ MODES[m.id] ?? m.id }}</option></select>
        </div>
        <div class="form-field">
          <label for="lc-pick-model">模型</label>
          <select id="lc-pick-model" v-model="model"><option value="">平台默认</option><option v-for="m in models" :key="m.id" :value="m.id">{{ m.name || m.id }}{{ m.default ? '（默认）' : '' }}</option></select>
        </div>
        <div v-if="efforts.length" class="form-field">
          <label for="lc-pick-effort">推理强度</label>
          <select id="lc-pick-effort" v-model="effort"><option value="">默认</option><option v-for="e in efforts" :key="e" :value="e">{{ e }}</option></select>
        </div>
        <div v-if="mode === 'dev'" class="form-field">
          <label for="lc-pick-env">开发环境</label>
          <select id="lc-pick-env" v-model="environment"><option value="" disabled>选一个开发环境</option><option v-for="e in environments" :key="e.id" :value="e.id">{{ e.name }}（{{ sandbox(e.sandbox) }}）</option></select>
        </div>
        <div class="lc-picker-foot">
          <button type="button" class="secondary-button" :disabled="targets.length >= GROUP_TARGETS.max" @click="add"><Plus aria-hidden="true" />添加</button>
          <p v-if="statusProblem" class="lc-note is-red" role="alert">{{ statusProblem }}</p>
          <p v-else-if="pickError" class="lc-note is-red" role="alert">{{ pickError }}</p>
        </div>
      </fieldset>

      <div class="form-field">
        <label for="lc-compare-text">问题</label>
        <textarea id="lc-compare-text" v-model="text" rows="5" maxlength="100000" placeholder="同时问它们的问题" />
      </div>
      <p v-if="error" class="lc-note is-red" role="alert">{{ error }}</p>
      <div class="lb-actions">
        <button type="button" class="secondary-button" :disabled="busy" @click="emit('cancel')">取消</button>
        <button type="submit" class="primary-button" :disabled="busy">{{ busy ? '正在发出…' : `发给 ${targets.length} 个模型` }}</button>
      </div>
    </div>
  </form>
</template>
