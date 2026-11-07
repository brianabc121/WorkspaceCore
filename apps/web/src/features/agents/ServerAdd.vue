<script setup lang="ts">
import { computed, reactive } from 'vue'
import { useAuthStore } from '../../stores/auth'
import { dataOf } from '../../services/api'
import { useAgentAction } from './action'
import { HOST_PATTERN, USERNAME_PATTERN, sshError } from './ssh'

// 添加服务器（T-0147）第一步：名称与 SSH 地址。工作台只读取主机公钥（不登录），
// 添加后进入服务器详情核对指纹、用一次密码准备服务器。
const emit = defineEmits<{ close: []; added: [serverId: string] }>()
const auth = useAuthStore(), scope = auth.workspaceId!
const { busy, error, act, header } = useAgentAction(scope, sshError)
const form = reactive({ name: '', host: '', port: 22, username: '' })
const valid = computed(() => form.name.trim().length > 0 && form.name.trim().length <= 100 && HOST_PATTERN.test(form.host.trim()) && USERNAME_PATTERN.test(form.username.trim()) && Number.isInteger(Number(form.port)) && Number(form.port) >= 1 && Number(form.port) <= 65535)

async function submit() {
  if (!valid.value) return
  let id = ''
  const ok = await act(async () => {
    id = dataOf(await auth.slowApi.POST('/api/v1/agents/servers', { params: { header }, body: { name: form.name.trim(), host: form.host.trim(), port: Number(form.port), username: form.username.trim() } })).server.id
  }, '已读取主机公钥：请在服务器上核对指纹')
  if (ok && id) emit('added', id)
}
</script>

<template>
  <form class="panel im-group-form" aria-label="添加服务器" @submit.prevent="submit">
    <h2>添加服务器</h2>
    <p class="form-help">
      填写一台 Linux 服务器的 SSH 地址与账号（root 或能用 sudo 的账号）。工作台先只读取它的主机公钥（不登录）；
      你在服务器上核对指纹后，再输入一次登录密码准备服务器，之后这台服务器上可以部署多个 OpenClaw 实例，每个实例一个目录、一个 Compose 项目、一个自己的网络。
    </p>
    <div class="im-group-add">
      <label class="form-field"><span>名称 *</span><input v-model.trim="form.name" maxlength="100" placeholder="例如：香港 2 核 4G" /></label>
      <label class="form-field"><span>服务器 IP 或域名 *</span><input v-model.trim="form.host" maxlength="253" autocomplete="off" placeholder="203.0.113.10" :aria-invalid="!!form.host && !HOST_PATTERN.test(form.host)" /></label>
      <label class="form-field"><span>SSH 端口 *</span><input v-model.number="form.port" type="number" min="1" max="65535" /></label>
      <label class="form-field"><span>SSH 账号 *</span><input v-model.trim="form.username" maxlength="32" autocomplete="off" placeholder="root 或能用 sudo 的账号" :aria-invalid="!!form.username && !USERNAME_PATTERN.test(form.username)" /></label>
    </div>
    <div class="form-actions">
      <button type="submit" class="primary-button" :disabled="busy || !valid">读取主机公钥</button>
      <button type="button" class="secondary-button" :disabled="busy" @click="emit('close')">取消</button>
    </div>
    <p v-if="error" role="alert">{{ error }}</p>
  </form>
</template>
