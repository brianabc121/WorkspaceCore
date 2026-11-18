<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { bridgeError, type LlmBridgeApi, type LoginState, type Slot } from './api'

// 在远程开发容器里登录一个平台（docs/v2/LLM_BRIDGE_REMOTE_ENV_PLAN.md 第 9 节）：会刷新的登录（ChatGPT、Claude 网页授权）
// 在容器里单独登录一次同一个账号。打开就开始：管理器在容器里起官方程序的登录，给出网址（Codex 另有验证码）；
// Claude 要把授权页上的授权码粘贴回来。进行中每 2 秒查一次进度；登录后管理器核对是同一个账号。
const props = defineProps<{ api: LlmBridgeApi; bridgeId: string; containerId: string; account: Slot; label: string; containerName: string }>()
const emit = defineEmits<{ close: [] }>()
const state = ref<LoginState | null>(null), error = ref(''), busy = ref(false), code = ref('')
let timer: ReturnType<typeof setInterval> | null = null
const pending = computed(() => !state.value || state.value.state === 'pending')
const url = computed(() => state.value?.verification_url ?? '')

function stopPolling() { if (timer) { clearInterval(timer); timer = null } }
async function poll() {
  try {
    state.value = await props.api.remoteLoginState(props.bridgeId, props.containerId, props.account)
    if (!pending.value) { stopPolling(); await props.api.invalidate('bridge', props.bridgeId, 'container-platforms', props.containerId) }
  } catch { /* 查不到进度就等下一次 */ }
}
async function start() {
  busy.value = true; error.value = ''
  try {
    state.value = await props.api.remoteLogin(props.bridgeId, props.containerId, props.account, 'start')
    stopPolling()
    if (pending.value) timer = setInterval(() => { void poll() }, 2000)
  } catch (e) { error.value = bridgeError(e) } finally { busy.value = false }
}
async function submitCode() {
  if (!code.value.trim()) return
  busy.value = true; error.value = ''
  try { state.value = await props.api.remoteLogin(props.bridgeId, props.containerId, props.account, 'code', code.value.trim()); code.value = '' } catch (e) { error.value = bridgeError(e) } finally { busy.value = false }
}
async function cancel() {
  stopPolling()
  try { await props.api.remoteLogin(props.bridgeId, props.containerId, props.account, 'cancel') } catch { /* 取消失败也关掉面板：登录 15 分钟后自己停 */ }
  emit('close')
}
onMounted(start)
onUnmounted(stopPolling)
</script>

<template>
  <section class="lb-remote-login" role="group" :aria-label="`在远程容器里登录 ${label}`">
    <h4>在远程容器「{{ containerName }}」里登录 {{ label }}</h4>
    <p v-if="error" role="alert" class="lb-warn is-red">{{ error }}</p>
    <p v-if="!state && busy" role="status" class="form-help">正在容器里启动官方程序的登录…（容器停着时要先启动，可能要一会儿）</p>
    <template v-if="state">
      <ol v-if="state.steps?.length" class="lb-remote-steps">
        <li v-for="s in state.steps" :key="s.key" :data-status="s.status">{{ s.detail || s.key }}</li>
      </ol>
      <div v-if="state.state === 'pending' && url" class="lb-login-open">
        <p>在你自己的浏览器里打开这个网址{{ state.user_code ? '，输入验证码' : '' }}并登录：</p>
        <p><a v-if="url.startsWith('https://')" class="asv-link lb-login-url" :href="url" target="_blank" rel="noopener noreferrer">{{ url }}</a><span v-else class="asv-code">{{ url }}</span></p>
        <p v-if="state.user_code">验证码：<strong class="lb-login-code">{{ state.user_code }}</strong></p>
        <form v-if="state.needs_code" class="lb-code-form" novalidate aria-label="提交授权码" @submit.prevent="submitCode">
          <label for="lb-remote-code">授权码</label>
          <input id="lb-remote-code" v-model="code" type="text" autocomplete="off" spellcheck="false" maxlength="4096" placeholder="粘贴授权页显示的整段授权码" :disabled="busy" />
          <button type="submit" class="primary-button" :disabled="busy || !code.trim()">{{ busy ? '正在提交…' : '提交授权码' }}</button>
        </form>
      </div>
      <p v-else-if="state.state === 'pending'" role="status" class="form-help">正在等容器里的官方程序给出登录网址…</p>
      <p v-if="state.state === 'done'" role="status" class="form-help">{{ label }} 已在远程容器里登录。{{ state.detail }}</p>
      <p v-if="state.state === 'failed'" role="alert" class="lb-warn is-red">登录没有完成：{{ state.detail || '原因不明' }}</p>
      <p class="form-help">登录凭据由官方程序自己保存在远程容器的数据卷里；删除容器前请到平台的设备管理里撤销这次登录。</p>
    </template>
    <div class="lb-actions">
      <button v-if="pending" type="button" class="secondary-button" @click="cancel">取消登录</button>
      <template v-else>
        <button v-if="state?.state === 'failed'" type="button" class="primary-button" :disabled="busy" @click="start">重新登录</button>
        <button type="button" class="secondary-button" @click="emit('close')">关闭</button>
      </template>
    </div>
  </section>
</template>
