<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { BLOCK_REASON, useServers } from '../../features/agents/servers'
import { PLATFORMS, deployError, newDeployRequestId, type LlmBridgeApi, type Platform } from './api'
import DeployJob from './DeployJob.vue'

// 页面上一键部署 MCP 管理器（docs/v2/LLM_BRIDGE_PLAN.md 第 20 节）：选一台“智能体服务器”页里初始化过的服务器，
// 工作台经 SSH 把管理器部署上去、连上它、登记好。只有 owner 能做。
// 和工作台同一台机器时管理器加入工作台的内部网络；不同机器时端口只开在私网地址上（公网地址不部署）。
const props = defineProps<{ api: LlmBridgeApi; scope: string; names: string[] }>()
const emit = defineEmits<{ deployed: [bridgeId: string, name: string]; cancel: [] }>()

const servers = useServers(props.scope)
const list = computed(() => (servers.data.value?.items ?? []).filter(s => s.kind === 'ssh'))
const ALL = PLATFORMS.map(p => p.id)
const form = reactive({ server: '', name: '', platforms: [...ALL] as Platform[] })
watch(list, items => { if (!items.some(s => s.id === form.server)) form.server = items.find(s => s.deployable)?.id ?? '' }, { immediate: true })
const chosen = computed(() => list.value.find(s => s.id === form.server) ?? null)

const jobId = ref<string | null>(null)
const job = props.api.useDeployment(jobId)
const busy = ref(false), error = ref('')
let requestId = newDeployRequestId()
const problem = computed(() => {
  const name = form.name.trim()
  if (!chosen.value) return list.value.length ? '选一台服务器。' : '还没有可以部署的服务器。'
  if (!chosen.value.deployable) return `这台服务器现在不能部署：${chosen.value.deploy_block_reason ? BLOCK_REASON[chosen.value.deploy_block_reason] : '暂不能部署'}。`
  if (!name || name.length > 40) return '名字要 1–40 个字。'
  if (props.names.includes(name)) return '已经有同名的 MCP 管理器，换一个名字。'
  if (!form.platforms.length) return '至少勾选一个要控制的平台。'
  return ''
})
async function submit() {
  if (busy.value) return
  if (problem.value) { error.value = problem.value; return }
  busy.value = true; error.value = ''
  try {
    const started = await props.api.deploy({ server_id: form.server, name: form.name.trim(), platforms: ALL.filter(p => form.platforms.includes(p)), request_id: requestId })
    jobId.value = started.id
  } catch (e) { error.value = deployError(e) } finally { busy.value = false }
}
// 任务结束：成功就告诉上层（刷新管理器列表并选中它）；失败留在这里，可以改了再来
watch(() => job.data.value?.status, status => {
  const data = job.data.value
  if (status === 'succeeded' && data?.bridge_id) emit('deployed', data.bridge_id, data.name)
})
function again() { jobId.value = null; requestId = newDeployRequestId(); error.value = '' }
</script>

<template>
  <div class="lb-deploy-form">
    <template v-if="jobId">
      <p v-if="job.isPending.value" role="status" class="form-help">正在读取进度…</p>
      <DeployJob v-else-if="job.data.value" :job="job.data.value" />
      <p v-if="job.error.value" role="alert" class="lb-warn is-red">读不到进度：{{ deployError(job.error.value) }}</p>
      <div class="lb-actions">
        <button v-if="job.data.value && job.data.value.status !== 'running'" type="button" class="secondary-button" @click="job.data.value.status === 'succeeded' ? emit('cancel') : again()">{{ job.data.value.status === 'succeeded' ? '完成' : '改一下再部署' }}</button>
      </div>
    </template>
    <form v-else novalidate aria-label="一键部署 MCP 管理器" @submit.prevent="submit">
      <p class="form-help">工作台登录你选的服务器，把 MCP 管理器部署上去并登记好，不用到服务器上敲命令。和工作台同一台机器时它加入工作台的内部网络；不同机器时端口只开在私网地址上。</p>
      <p v-if="servers.isPending.value" role="status" class="form-help">正在读取服务器…</p>
      <p v-else-if="servers.error.value" role="alert" class="lb-warn is-red">读不到服务器列表：{{ deployError(servers.error.value) }}</p>
      <div v-else-if="!list.length" class="lb-empty">
        <p><strong>还没有可以部署的服务器。</strong></p>
        <p>先到“智能体服务器”页添加一台服务器（填地址、账号、密码），核对指纹并初始化，然后回到这里。工作台自己所在的服务器也可以添加。</p>
        <p><RouterLink class="asv-link" to="/agent-servers">去智能体服务器页</RouterLink></p>
      </div>
      <template v-else>
        <div class="lb-grid">
          <div class="form-field">
            <label for="lb-deploy-server">部署到哪台服务器</label>
            <select id="lb-deploy-server" v-model="form.server">
              <option value="" disabled>选一台服务器</option>
              <option v-for="s in list" :key="s.id" :value="s.id">{{ s.name }}{{ s.deployable ? '' : `（不能部署：${s.deploy_block_reason ? BLOCK_REASON[s.deploy_block_reason] : '暂不能部署'}）` }}</option>
            </select>
            <p class="form-help">列的是“智能体服务器”页里用账号密码添加的服务器。<RouterLink class="asv-link" to="/agent-servers">管理服务器</RouterLink></p>
          </div>
          <div class="form-field"><label for="lb-deploy-name">名字</label><input id="lb-deploy-name" v-model="form.name" maxlength="40" required placeholder="例如：2 号" /><p class="form-help">智能体用这个名字指定走哪个 MCP 管理器。</p></div>
        </div>
        <fieldset class="lb-checks" aria-label="控制的平台">
          <legend>控制的平台</legend>
          <label v-for="p in PLATFORMS" :key="p.id" class="check-field"><input v-model="form.platforms" type="checkbox" :value="p.id" />{{ p.name }}</label>
        </fieldset>
        <p class="form-help">部署好之后在“平台与账号”里登录这些平台的账号。第一次部署要在服务器上构建镜像，通常要几分钟。</p>
      </template>
      <p v-if="error" role="alert" class="lb-warn is-red">{{ error }}</p>
      <div class="lb-actions">
        <button type="button" class="secondary-button" :disabled="busy" @click="emit('cancel')">取消</button>
        <button v-if="list.length" type="submit" class="primary-button" :disabled="busy">{{ busy ? '正在开始…' : '开始部署' }}</button>
      </div>
    </form>
  </div>
</template>
