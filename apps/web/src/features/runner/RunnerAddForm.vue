<script setup lang="ts">
import { ref } from 'vue'
import { dataOf, type Schema } from '../../services/api'
import { useAuthStore } from '../../stores/auth'
import { useRunnerAction, type RunnerHost } from './api'

// 添加容器服务器（T-0277：和智能体服务器页一样放在右侧一栏 / 抽屉里）：名称与连工作台的入口。
// 添加后打开这台服务器的详情，在“连接与部署”里填 SSH 地址部署（或下载接入包手动部署）。
const emit = defineEmits<{ saved: [host: RunnerHost]; close: [] }>()
const auth = useAuthStore(), scope = auth.workspaceId!
const { busy, error, act, header } = useRunnerAction(scope)
const name = ref('容器服务器'), ingress = ref<Schema['RunnerHostCreate']['ingress']>('intranet')

async function add() {
  let created: RunnerHost | null = null
  const ok = await act(async () => {
    created = dataOf(await auth.api.POST('/api/v1/runner/hosts', { params: { header }, body: { name: name.value.trim(), ingress: ingress.value, backend: 'docker' } }))
  })
  if (ok && created) emit('saved', created)
}
</script>

<template>
  <form class="asv-form cs-add" aria-label="添加容器服务器" @submit.prevent="add">
    <p class="form-help">用一台专用的 Linux 服务器（x86_64 或 ARM64，内核 5.6 以上）。添加后在这台服务器的“连接与部署”里填 SSH 地址，工作台会装好锁定版本的 Docker、gVisor 和 runner-agent；runner-agent 主动连工作台的接入入口，服务器不用开入站端口。</p>
    <label class="form-field"><span>名称</span><input v-model="name" maxlength="64" required /></label>
    <label class="form-field"><span>连工作台的入口</span><select v-model="ingress"><option value="intranet">内网入口</option><option value="public">外网入口（另发客户端证书）</option></select></label>
    <p class="form-help">服务器和工作台在同一个内网选“内网入口”；要经公网连回来选“外网入口”。</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <div class="form-actions">
      <button type="submit" class="primary-button" :disabled="busy || !name.trim()">添加</button>
      <button type="button" class="secondary-button" @click="emit('close')">取消</button>
    </div>
  </form>
</template>
