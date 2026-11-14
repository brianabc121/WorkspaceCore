<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { ArrowLeft } from '@lucide/vue'
import { ApiError } from '../../../services/api'
import { MODES, SANDBOX, platformName, type Bridge, type Platform } from '../api'
import { chatError, newRequestId, type ChatCreateBody, type LlmChatApi, type PlatformChoice } from './chatApi'

// 新建会话：管理器 → 平台（只列这个管理器控制的；没登录的不能选）→ 用法 → 模型、推理强度 → 开发环境 → 第一条消息。
const props = defineProps<{
  chat: LlmChatApi; bridges: Bridge[]; manager: Bridge; choices: PlatformChoice[]
  statusLoading: boolean; statusProblem: string; narrow: boolean
}>()
const emit = defineEmits<{ 'update:managerId': [id: string]; created: [sessionId: string, draft: string, error: string]; cancel: [] }>()

const MODE_HELP: Record<string, string> = { chat: '普通问答，不动代码。', dev: '在选定的开发环境里读写代码、执行命令。' }
const platform = ref<Platform | null>(null), mode = ref<'chat' | 'dev'>('chat')
const model = ref(''), effort = ref(''), environment = ref(''), title = ref(''), text = ref('')
const usable = computed(() => props.choices.filter(c => c.loggedIn))
const choice = computed(() => props.choices.find(c => c.id === platform.value) ?? null)
const modes = computed(() => choice.value?.modes ?? [])

// 换管理器或状态读到之后：默认选第一个已登录的平台、它的第一种能用的用法
watch(() => props.choices, () => { if (!usable.value.some(c => c.id === platform.value)) platform.value = (usable.value[0]?.id as Platform | undefined) ?? null }, { immediate: true })
watch(modes, list => { if (!list.some(m => m.id === mode.value && m.available)) mode.value = list.find(m => m.available)?.id ?? list[0]?.id ?? 'chat' }, { immediate: true })

const managerId = computed(() => props.manager.id)
const options = props.chat.useOptions(managerId, platform, mode)
const models = computed(() => options.data.value?.models ?? [])
const environments = computed(() => (options.data.value?.environments ?? []).filter(e => e.enabled))
const efforts = computed(() => (models.value.find(m => m.id === model.value) ?? models.value.find(m => m.default))?.efforts ?? [])
watch([platform, mode], () => { model.value = ''; effort.value = ''; environment.value = '' })
watch(environments, list => { if (!list.some(e => e.id === environment.value)) environment.value = list.length === 1 ? list[0]!.id : '' })
watch(efforts, list => { if (!list.includes(effort.value)) effort.value = '' })
const sandbox = (id: string) => (SANDBOX as Record<string, string>)[id] ?? id

const busy = ref(false), error = ref('')
let pending: { key: string; id: string } | null = null
const problem = computed(() => {
  if (!props.manager.enabled) return '这个 MCP 管理器已停用，不能新建会话。'
  if (!platform.value || !choice.value) return usable.value.length ? '选一个平台。' : '这个 MCP 管理器上还没有登录任何平台。'
  if (!modes.value.find(m => m.id === mode.value)?.available) return '这个平台现在不能用这种用法。'
  if (mode.value === 'dev' && !environment.value) return environments.value.length ? '开发会话要选一个开发环境。' : '这个平台还没有可用的开发环境：先到“管理器与账号”的“开发环境”里添加。'
  return ''
})
async function submit() {
  if (busy.value) return
  if (problem.value) { error.value = problem.value; return }
  const first = text.value.trim()
  const body: ChatCreateBody = {
    platform: platform.value!, mode: mode.value, title: title.value.trim(),
    ...(model.value ? { model: model.value } : {}), ...(effort.value ? { effort: effort.value } : {}),
    ...(mode.value === 'dev' ? { environment: environment.value } : {}),
  }
  if (first) {
    const key = JSON.stringify([props.manager.id, body, first])
    if (pending?.key !== key) pending = { key, id: newRequestId() }
    Object.assign(body, { text: first, request_id: pending.id })
  }
  busy.value = true; error.value = ''
  try {
    const out = await props.chat.create(props.manager.id, body)
    pending = null
    // 会话建好了但第一条消息没发出去：带着原文进会话，可以在那里重新发
    emit('created', out.session.session_id, out.send_error ? first : '', out.send_error ? `会话建好了，但第一条消息没有发出去：${out.send_error}` : '')
  } catch (e) {
    const unknown = e instanceof ApiError && ['WRITE_UNKNOWN', 'EXTERNAL_TIMEOUT'].includes(e.code)
    if (unknown) void props.chat.invalidateSessions(props.manager.id)
    error.value = unknown ? '连接中断或超时，不确定会话有没有建好：先回会话列表看一下（可能已经在里面），再决定要不要重新新建。' : chatError(e)
  } finally { busy.value = false }
}
</script>

<template>
  <form class="lc-new-form" novalidate aria-label="新建会话" @submit.prevent="submit">
    <header class="lc-head">
      <button v-if="narrow" type="button" class="lc-icon" aria-label="返回会话列表" @click="emit('cancel')"><ArrowLeft aria-hidden="true" /></button>
      <div class="lc-head-main"><h2>新建会话</h2><p class="lc-head-meta">选好管理器、平台和用法，写下第一条消息。</p></div>
    </header>
    <div class="lc-new-body">
      <div class="form-field">
        <label for="lc-new-manager">MCP 管理器</label>
        <select id="lc-new-manager" :value="manager.id" @change="emit('update:managerId', ($event.target as HTMLSelectElement).value)">
          <option v-for="b in bridges" :key="b.id" :value="b.id">{{ b.name }}{{ b.enabled ? '' : '（已停用）' }}</option>
        </select>
        <p class="form-help">每个管理器有自己的一套账号：会话用的是这个管理器上登录的账号。</p>
      </div>

      <p v-if="statusLoading" role="status" class="lc-note">正在读取这个管理器的平台与账号…</p>
      <p v-else-if="statusProblem" role="alert" class="lc-note is-red">{{ statusProblem }}</p>
      <template v-else>
        <fieldset class="lc-choices" aria-label="平台">
          <legend>平台</legend>
          <label v-for="c in choices" :key="c.id" class="lc-choice" :class="{ 'is-off': !c.loggedIn }">
            <input v-model="platform" type="radio" name="lc-platform" :value="c.id" :disabled="!c.loggedIn" />
            <span><strong>{{ platformName(c.id) }}</strong><small>{{ c.loggedIn ? '已登录' : '未登录' }}</small></span>
          </label>
        </fieldset>
        <p v-if="choices.some(c => !c.loggedIn)" class="form-help">没登录的平台不能选。<RouterLink :to="{ path: '/llm_bridge', query: { view: 'manage' } }" class="asv-link">去登录</RouterLink></p>
        <p v-if="!choices.length" class="lc-note">这个管理器没有控制任何平台：到“管理器与账号”里勾选。</p>

        <fieldset v-if="choice" class="lc-choices" aria-label="用法">
          <legend>用法</legend>
          <label v-for="m in modes" :key="m.id" class="lc-choice" :class="{ 'is-off': !m.available }">
            <input v-model="mode" type="radio" name="lc-mode" :value="m.id" :disabled="!m.available" />
            <span><strong>{{ MODES[m.id] ?? m.id }}</strong><small>{{ m.available ? MODE_HELP[m.id] : m.note || '现在不能用' }}</small></span>
          </label>
        </fieldset>

        <template v-if="choice">
          <div class="lb-grid">
            <div class="form-field">
              <label for="lc-new-model">模型</label>
              <select id="lc-new-model" v-model="model">
                <option value="">平台默认</option>
                <option v-for="m in models" :key="m.id" :value="m.id">{{ m.name || m.id }}{{ m.default ? '（默认）' : '' }}</option>
              </select>
              <p v-if="options.isFetching.value" class="form-help" role="status">正在读取可选的模型…</p>
              <p v-else-if="options.data.value?.models_error" class="form-help">读不到模型列表（{{ options.data.value.models_error }}）：可以先用平台默认的模型。</p>
            </div>
            <div v-if="efforts.length" class="form-field">
              <label for="lc-new-effort">推理强度</label>
              <select id="lc-new-effort" v-model="effort"><option value="">默认</option><option v-for="e in efforts" :key="e" :value="e">{{ e }}</option></select>
            </div>
          </div>
          <div v-if="mode === 'dev'" class="form-field">
            <label for="lc-new-env">开发环境</label>
            <select id="lc-new-env" v-model="environment" required>
              <option value="" disabled>选一个开发环境</option>
              <option v-for="e in environments" :key="e.id" :value="e.id">{{ e.name }}（{{ sandbox(e.sandbox) }}{{ e.network ? '，可联网' : '' }}）</option>
            </select>
            <p class="form-help">开发环境由 owner 在“管理器与账号”里定好：用哪个仓库、能不能写、能不能执行命令。</p>
          </div>
          <div class="form-field">
            <label for="lc-new-title">标题（可以不填）</label>
            <input id="lc-new-title" v-model="title" maxlength="120" placeholder="不填时用第一条消息的开头" />
          </div>
          <div class="form-field">
            <label for="lc-new-text">第一条消息</label>
            <textarea id="lc-new-text" v-model="text" rows="5" maxlength="100000" placeholder="想问什么、想让它做什么" />
            <p class="form-help">可以先不写：只建好会话，之后再发。</p>
          </div>
        </template>
      </template>
      <p v-if="options.error.value && choice" class="lc-note is-red" role="alert">读不到可选项：{{ chatError(options.error.value) }}</p>
      <p v-if="error" class="lc-note is-red" role="alert">{{ error }}</p>
      <div class="lb-actions">
        <button type="button" class="secondary-button" :disabled="busy" @click="emit('cancel')">取消</button>
        <button type="submit" class="primary-button" :disabled="busy || statusLoading">{{ busy ? '正在新建…' : text.trim() ? '新建并发送' : '新建会话' }}</button>
      </div>
    </div>
  </form>
</template>
