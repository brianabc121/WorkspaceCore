<script setup lang="ts">
import { computed, reactive, ref, useId, watch } from 'vue'
import { useQueryClient } from '@tanstack/vue-query'
import { CircleCheck, CircleDashed, Eye, EyeOff, TriangleAlert } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf } from '../../services/api'
import { CONNECTION_HELP, CONNECTION_STATUS, endpoint, putServerDetail, serversKey, type Server, type ServerDetail } from './servers'
import { HOST_PATTERN, USERNAME_PATTERN, hostKeyCommand, sshError } from './ssh'

// 添加 / 更新服务器的连接信息（T-0181，改造规范第 4.1 节）：只要 IP、SSH 端口（默认 22）、登录账号、登录密码，名称可选。
// “测试并保存”串联三步：保存（工作台只读取主机公钥、不登录）→ owner 核对指纹并确认 → 用保存的密码测试连接；
// 每一步都用上一步返回的最新 server.version。没确认指纹时不能测试（确认前密码不会发给服务器）。
// 密码只在本组件的内存里：提交时立即清空，关闭或切换服务器时组件卸载 / 清空；不进地址栏、浏览器存储与查询缓存。
// mode：create 添加；credentials 补充 / 更新登录密码（保留初始化记录与实例）；address 更换地址（要重新核对指纹）。
type Mode = 'create' | 'credentials' | 'address'
const props = defineProps<{ mode: Mode; server?: Server | null }>()
const emit = defineEmits<{ close: []; saved: [serverId: string] }>()
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id, cache = useQueryClient()
const header = { 'X-Workspace-Id': scope }
const active = () => auth.user?.id === user && auth.workspaceId === scope
const ids = { name: useId(), host: useId(), port: useId(), username: useId(), password: useId() }

const form = reactive({ name: '', host: props.server?.ssh?.host ?? '', port: props.server?.ssh?.port ?? 22, username: props.server?.ssh?.username ?? '' })
const password = ref(''), reveal = ref(false), checked = ref(false)
const stage = ref<'form' | 'verify' | 'result'>('form')
const current = ref<Server | null>(props.server ?? null)
const warning = ref(''), busy = ref(false), error = ref(''), tested = ref(false)
// 切换到另一台服务器：清空密码，从头开始
watch(() => props.server?.id, (next, before) => { if (next !== before) { password.value = ''; reveal.value = false; checked.value = false; stage.value = 'form'; current.value = props.server ?? null } })

const title = computed(() => props.mode === 'create' ? '添加服务器' : props.mode === 'address' ? '更换地址' : props.server?.ssh?.password_set ? '更新登录密码' : '补充服务器登录密码')
const hostValid = computed(() => HOST_PATTERN.test(form.host.trim()))
const userValid = computed(() => USERNAME_PATTERN.test(form.username.trim()))
const portValid = computed(() => Number.isInteger(Number(form.port)) && Number(form.port) >= 1 && Number(form.port) <= 65535)
const passwordValid = computed(() => password.value.length >= 1 && password.value.length <= 1024)
const valid = computed(() => passwordValid.value && (props.mode === 'credentials' || (hostValid.value && userValid.value && portValid.value && form.name.trim().length <= 100)))
const ssh = computed(() => current.value?.ssh ?? null)
const status = computed(() => ssh.value ? CONNECTION_STATUS[ssh.value.connection_status] : null)
const connected = computed(() => ssh.value?.connection_status === 'connected')
const CHECKLIST = ['检测系统与权限', '安装 Docker 与 Compose（已有兼容版本直接复用）', '准备部署目录和基础依赖', '验证平台连通性']

async function step<T>(effect: () => Promise<T>): Promise<T | null> {
  if (!active()) return null
  busy.value = true; error.value = ''
  try { const result = await effect(); return active() ? result : null }
  catch (cause) {
    if (active()) error.value = sshError(cause)
    // 结果未知：服务器可能已经保存了，刷新列表让 owner 看到真实状态（不自动重发，密码也已经清空）
    if (cause instanceof ApiError && cause.code === 'WRITE_UNKNOWN') void cache.invalidateQueries({ queryKey: serversKey(user, scope) })
    return null
  } finally { busy.value = false }
}
function apply(detail: ServerDetail) {
  current.value = detail.server
  warning.value = detail.warning ?? ''
  putServerDetail(cache, user, scope, detail)
}

async function submit() {
  if (!valid.value || busy.value) return
  // 密码随这一次请求发出，发出前就从输入框清掉（首尾空格原样发送）
  const secret = password.value
  password.value = ''; reveal.value = false
  const target = current.value
  const detail = await step(async () => {
    const key = { ...header, 'Idempotency-Key': crypto.randomUUID() }
    if (props.mode === 'create') {
      const name = form.name.trim()
      return dataOf(await auth.slowApi.POST('/api/v1/agents/servers', { params: { header: key }, body: { ...(name ? { name } : {}), host: form.host.trim(), port: Number(form.port), username: form.username.trim(), login_password: secret } }))
    }
    if (!target) throw new ApiError(404, 'NOT_FOUND', '服务器不存在')
    if (props.mode === 'address') return dataOf(await auth.slowApi.PUT('/api/v1/agents/servers/{server_id}/address', { params: { header: key, path: { server_id: target.id } }, body: { host: form.host.trim(), port: Number(form.port), username: form.username.trim(), login_password: secret, version: target.version } }))
    return dataOf(await auth.api.PUT('/api/v1/agents/servers/{server_id}/credentials', { params: { header: key, path: { server_id: target.id } }, body: { login_password: secret, version: target.version } }))
  })
  if (!detail) return
  apply(detail)
  if (props.mode === 'create') emit('saved', detail.server.id)
  // 指纹已经确认过（只换了密码）：直接测试；否则先核对指纹
  if (detail.server.ssh?.host_key_confirmed) await test()
  else stage.value = 'verify'
}

async function confirmAndTest() {
  const s = current.value
  if (!s?.ssh || !checked.value || busy.value) return
  const fingerprint = s.ssh.host_key_fingerprint
  const detail = await step(async () => dataOf(await auth.api.POST('/api/v1/agents/servers/{server_id}/confirm', { params: { header, path: { server_id: s.id } }, body: { version: s.version, fingerprint } })))
  if (!detail) return
  apply(detail)
  await test()
}

async function test() {
  const s = current.value
  if (!s) return
  // 没确认指纹不能测试：确认之前密码不会发给服务器
  if (!s.ssh?.host_key_confirmed) { stage.value = 'verify'; return }
  tested.value = false
  const detail = await step(async () => dataOf(await auth.slowApi.POST('/api/v1/agents/servers/{server_id}/test-connection', { params: { header: { ...header, 'Idempotency-Key': crypto.randomUUID() }, path: { server_id: s.id } }, body: { version: s.version } })))
  stage.value = 'result'
  if (detail) { apply(detail); tested.value = true }
}
</script>

<template>
  <div class="asv-connection">
    <form v-if="stage === 'form'" class="asv-form" :aria-label="title" @submit.prevent="submit">
      <p v-if="mode === 'create'" class="form-help">填写连接信息，保存后即可初始化。工作台先只读取服务器的主机公钥（不登录），你核对指纹后才会用密码登录测试。</p>
      <p v-else-if="mode === 'credentials'" class="form-help">
        {{ server?.ssh?.password_set ? '替换保存的登录密码：初始化记录与上面的智能体都保留，保存后重新测试连接。' : '这台服务器是旧方案添加的，工作台从没保存过它的登录密码（不能从旧专用私钥还原）。补充后才能初始化、部署与运维；上面已有的智能体照常运行，不会重装。' }}
      </p>
      <p v-else class="form-help">更换 IP、端口或账号后要重新核对主机指纹、重新检测环境。这台服务器上有经平台部署的智能体时不能更换（不会把原来的智能体挂到新机器上）。</p>

      <dl v-if="mode === 'credentials' && server?.ssh" class="ws-facts asv-target">
        <dt>服务器</dt><dd>{{ server.name }}</dd>
        <dt>登录</dt><dd>{{ server.ssh.username }}@{{ endpoint(server.ssh.host, server.ssh.port) }}</dd>
      </dl>
      <template v-else>
        <div v-if="mode === 'create'" class="form-field">
          <label :for="ids.name">服务器名称（可选）</label>
          <input :id="ids.name" v-model="form.name" maxlength="100" autocomplete="off" placeholder="不填时用 IP:端口" />
        </div>
        <div class="form-field">
          <label :for="ids.host">IP 地址 *</label>
          <input :id="ids.host" v-model.trim="form.host" maxlength="253" autocomplete="off" inputmode="url" placeholder="例如 203.0.113.10（也可以填域名）" :aria-invalid="!!form.host && !hostValid" />
        </div>
        <div class="form-field">
          <label :for="ids.port">SSH 端口 *</label>
          <input :id="ids.port" v-model.number="form.port" type="number" min="1" max="65535" :aria-invalid="!portValid" />
        </div>
        <div class="form-field">
          <label :for="ids.username">登录账号 *</label>
          <input :id="ids.username" v-model.trim="form.username" maxlength="32" autocomplete="off" placeholder="root 或能用 sudo 的账号" :aria-invalid="!!form.username && !userValid" />
        </div>
      </template>
      <div class="form-field">
        <label :for="ids.password">登录密码 *</label>
        <div class="asv-password">
          <input :id="ids.password" v-model="password" :type="reveal ? 'text' : 'password'" maxlength="1024" autocomplete="new-password" spellcheck="false" />
          <button type="button" class="asv-reveal" :aria-label="reveal ? '隐藏密码' : '显示密码'" :aria-pressed="reveal" @click="reveal = !reveal"><EyeOff v-if="reveal" aria-hidden="true" /><Eye v-else aria-hidden="true" /></button>
        </div>
        <p class="form-help">密码加密保存，只用于登录这台服务器（需要时同一个密码用于 sudo）；任何页面和接口都不会再显示它。</p>
      </div>

      <section v-if="mode === 'create'" class="asv-checklist" aria-label="初始化将完成">
        <h3>初始化将完成</h3>
        <ul>
          <li v-for="item in CHECKLIST" :key="item"><CircleDashed aria-hidden="true" /><span>{{ item }}</span><span class="asv-pending">待执行</span></li>
        </ul>
        <p class="form-help">“测试并保存”只核对身份、测试登录，不安装任何东西；初始化在服务器详情里单独执行，完成后再到 OpenClaw 智能体页部署。</p>
      </section>

      <p v-if="error" role="alert">{{ error }}</p>
      <div class="form-actions asv-actions">
        <button type="button" class="secondary-button" :disabled="busy" @click="emit('close')">取消</button>
        <button type="submit" class="primary-button" :disabled="busy || !valid">{{ busy ? '正在保存…' : mode === 'create' ? '测试并保存' : '保存并测试连接' }}</button>
      </div>
    </form>

    <section v-else-if="stage === 'verify' && current && ssh" class="asv-form" role="group" aria-label="核对主机指纹">
      <p class="form-help">已保存「{{ current.name }}」并读取到它的主机公钥（还没有登录，密码还没发给服务器）。确认是你的服务器后再测试连接。</p>
      <p>主机公钥（{{ ssh.host_key_algorithm }}）指纹：</p>
      <p><code class="asv-fingerprint" data-testid="ssh-fingerprint">{{ ssh.host_key_fingerprint }}</code></p>
      <p class="form-help">核对方法：用云服务商的网页控制台（或其他你信任的方式）登录这台服务器，运行下面的命令，输出里 SHA256: 开头的那一段必须和上面完全一致。不一致时不要确认——可能连到了别的机器。</p>
      <pre class="ai-arguments">{{ hostKeyCommand(ssh.host_key_algorithm) }}</pre>
      <label class="check-field"><input v-model="checked" type="checkbox" /> 我已在服务器上核对，指纹完全一致</label>
      <p v-if="error" role="alert">{{ error }}</p>
      <div class="form-actions asv-actions">
        <button type="button" class="secondary-button" :disabled="busy" @click="emit('close')">稍后再核对</button>
        <button type="button" class="primary-button" :disabled="busy || !checked" @click="confirmAndTest">{{ busy ? '正在测试…' : '确认指纹并测试连接' }}</button>
      </div>
    </section>

    <section v-else-if="stage === 'result'" class="asv-form" aria-label="连接测试结果">
      <template v-if="tested && status && ssh">
        <p class="asv-result" :class="`tone-${status.tone}`"><CircleCheck v-if="connected" aria-hidden="true" /><TriangleAlert v-else aria-hidden="true" /><strong>{{ connected ? '连接正常' : status.label }}</strong></p>
        <p class="form-help">{{ CONNECTION_HELP[ssh.connection_status] }}</p>
        <div v-if="warning" class="ws-callout tone-amber" role="note"><strong>需要处理</strong><p>{{ warning }}</p></div>
        <p v-if="connected" class="form-help">下一步：在服务器详情里点“初始化服务器”，检测或安装 Docker 与 Compose、准备部署目录。</p>
      </template>
      <p v-if="error" role="alert">{{ error }}</p>
      <div class="form-actions asv-actions">
        <button v-if="!connected || error" type="button" class="secondary-button" :disabled="busy" @click="test">{{ busy ? '正在测试…' : '重新测试' }}</button>
        <button type="button" class="primary-button" :disabled="busy" @click="emit('close')">完成</button>
      </div>
    </section>
  </div>
</template>
