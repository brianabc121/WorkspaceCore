<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { deployError, needsSudoPassword, newDeployRequestId, type Bridge, type LlmBridgeApi, type ManagedAction } from './api'

// 工作台部署的 MCP 管理器上的三种操作（docs/v2/LLM_BRIDGE_DEPLOY_PLAN.md 第 3 节）：重新部署、重启、卸载。
// 先说明会发生什么，确认后开始一个后台任务（进度由上层显示）。只有 owner 能做。
// 登录密码：部署时记下这台服务器用 Docker 要 sudo 密码（needs_sudo）就先问；没记下、服务器却回答要（409）时也在这里补填再发。
// 密码只在提交时发出去，不保存；提交后输入框清空。
// 卸载可以选“同时删除数据卷”和“只删除登记”（不连服务器；服务器连不上或已经从工作台移除时用）。
const props = defineProps<{ api: LlmBridgeApi; bridge: Bridge; action: ManagedAction; askPassword?: boolean; hint?: string }>()
const emit = defineEmits<{ started: [jobId: string]; cancel: [] }>()

const TEXT: Record<ManagedAction, { title: string; body: string; submit: string }> = {
  redeploy: { title: '重新部署', submit: '重新部署', body: '用工作台现在带的版本重新上传、构建并启动它：正在进行的回合会中断。数据卷、令牌、端口和通道都不变，登录状态和会话记录都保留。' },
  restart: { title: '重启', submit: '重启', body: '在服务器上重启它的容器，再确认工作台连得上：正在进行的回合会中断。登录状态和会话记录都保留。' },
  uninstall: { title: '卸载', submit: '卸载', body: '工作台登录它所在的服务器，停掉并删除这个容器、删除部署目录和转发密钥，再从工作台去掉它。只允许用它的实例，之后一个 MCP 管理器都用不了，要到“实例权限”里重新选。' },
}
const text = computed(() => TEXT[props.action])
const install = computed(() => props.bridge.deployment ?? null)
// 服务器已经从工作台移除：工作台登录不上去，只能删除登记
const removed = computed(() => !install.value?.target_id)
const form = reactive({ data: false, force: removed.value, password: '' })
const force = computed(() => props.action === 'uninstall' && (form.force || removed.value))
const asked = ref(!!props.askPassword)
const needsPassword = computed(() => !force.value && (!!install.value?.needs_sudo || asked.value))
const busy = ref(false), error = ref(props.hint ?? '')
let requestId = newDeployRequestId()

async function submit() {
  if (busy.value) return
  if (needsPassword.value && !form.password) { error.value = '填写这台服务器的登录密码（只用这一次，不保存）。'; return }
  busy.value = true; error.value = ''
  const id = props.bridge.id, password = needsPassword.value ? form.password : ''
  try {
    const job = props.action === 'redeploy' ? await props.api.redeploy(id, requestId, password)
      : props.action === 'restart' ? await props.api.restart(id, requestId, password)
        : await props.api.uninstall(id, requestId, { remove_data: !force.value && form.data, force: force.value, sudo_password: password })
    form.password = '' // 密码不留在页面上
    emit('started', job.id)
  } catch (e) {
    if (needsSudoPassword(e)) {
      // 服务器的登录账号用 Docker 要 sudo 密码：显示密码框，填了再发（这一次没有开始任务，换一个幂等号）
      asked.value = true; requestId = newDeployRequestId()
      error.value = `这台服务器的登录账号用 Docker 要 sudo 密码：在下面填写登录密码，再点“${force.value ? '只删除登记' : text.value.submit}”（只用这一次，不保存）。`
    } else error.value = deployError(e)
  } finally { busy.value = false }
}
</script>

<template>
  <form class="asv-form lb-form" novalidate :aria-label="`${text.title}${bridge.name}`" @submit.prevent="submit">
    <h3>{{ text.title }}「{{ bridge.name }}」</h3>
    <p>{{ text.body }}</p>
    <template v-if="action === 'uninstall'">
      <label class="check-field"><input v-model="form.data" type="checkbox" :disabled="force" />同时删除数据卷（各平台的登录状态和会话记录，不能恢复）</label>
      <label class="check-field"><input v-model="form.force" type="checkbox" :disabled="removed" />只删除登记（服务器连不上时用）</label>
      <p v-if="removed" role="note" class="lb-warn">服务器已从工作台移除，工作台登录不上去：只能删除登记。</p>
      <p v-if="force" class="form-help">不连服务器，只从工作台去掉这个管理器：它的容器、数据卷、部署目录（里面有管理令牌）和转发密钥都留在服务器上，删除后会列出来，要到服务器上手动清理。</p>
      <p v-else class="form-help">不勾选“同时删除数据卷”时，数据卷留在服务器上。</p>
    </template>
    <div v-if="needsPassword" class="form-field">
      <label :for="`lb-op-password-${bridge.id}`">登录密码</label>
      <input :id="`lb-op-password-${bridge.id}`" v-model="form.password" type="password" autocomplete="off" maxlength="256" />
      <p class="form-help">这台服务器的登录账号用 Docker 要 sudo 密码：只用这一次，不保存。</p>
    </div>
    <p v-if="error" role="alert" class="lb-warn is-red">{{ error }}</p>
    <div class="lb-actions">
      <button type="button" class="secondary-button" :disabled="busy" @click="emit('cancel')">取消</button>
      <button type="submit" class="primary-button" :class="{ 'lb-danger-button': action === 'uninstall' }" :disabled="busy">{{ busy ? '正在开始…' : force ? '只删除登记' : text.submit }}</button>
    </div>
  </form>
</template>
