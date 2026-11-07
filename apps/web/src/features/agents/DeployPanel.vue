<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf, type Schema } from '../../services/api'
import { formatTime } from '../time'
import { useAgentAction } from './action'
import { SERVER_KIND, deployable, deploymentKey, prepared, useDeployment, useServers } from './servers'
import { sshError } from './ssh'
import SshJob from './SshJob.vue'

// 实例卡片里的“部署到服务器”（T-0147，替换原来每个实例一台服务器的 SSH 自动部署）：
// 选一台添加好的服务器部署；服务器准备好了（装了工作台的专用公钥）就不用密码，没准备的带一次密码。
// 只替换这个实例自己的目录与 Compose 项目，同一台服务器上的其他实例不受影响。
// 手动接入的实例可以手动指定所在服务器（以手动为准，D3），或改回按上报自动识别。
const props = defineProps<{ instance: Schema['AgentInstanceOut'] }>()
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id, cache = useQueryClient()
const state = useDeployment(scope, () => props.instance.id)
const servers = useServers(scope)
const { busy, error, notice, failure, act, refresh, header } = useAgentAction(scope, sshError)
const supported = computed(() => props.instance.network_mode === 'public')
const disabled = computed(() => props.instance.status === 'disabled')
const deployment = computed(() => state.data.value?.deployment ?? null)
const current = computed(() => state.data.value?.server ?? null)
const job = computed(() => state.data.value?.job ?? null)
const running = computed(() => job.value?.status === 'running')
const all = computed(() => servers.data.value?.items ?? [])
const targets = computed(() => all.value.filter(deployable))
const choice = ref(''), password = ref(''), removeFiles = ref(false), warning = ref(''), assignTo = ref<string>('auto')
const chosen = computed(() => deployment.value ? all.value.find(s => s.id === deployment.value?.server_id) ?? null : targets.value.find(s => s.id === choice.value) ?? null)
const needsPassword = computed(() => !!chosen.value && !prepared(chosen.value))
const path = computed(() => ({ instance_id: props.instance.id }))

// 选的服务器变了就清掉密码：一台服务器的密码绝不发给另一台
watch(() => chosen.value?.id, () => { password.value = '' })
watch(targets, list => { if (!choice.value && list.length) choice.value = (list.find(prepared) ?? list[0]).id }, { immediate: true })
watch(() => [props.instance.server_id, props.instance.server_manual] as const, ([id, manual]) => { assignTo.value = manual && id ? id : 'auto' }, { immediate: true })
// 部署 / 卸载结束：实例的状态、凭证、所在服务器和运维是否可用都变了
watch(() => job.value?.status, (now, before) => {
  if (before !== 'running' || !now || now === 'running') return
  notice.value = ''
  void cache.invalidateQueries({ queryKey: ['agents', user, scope, 'instances'] })
  void cache.invalidateQueries({ queryKey: ['agents', user, scope, 'servers'] })
  void cache.invalidateQueries({ queryKey: ['agents', user, scope, 'ops', 'instance', props.instance.id] })
})

function put(next: Schema['AgentDeploymentState']) { cache.setQueryData(deploymentKey(user, scope, props.instance.id), next) }
function settle() { if (failure.value instanceof ApiError && failure.value.code === 'WRITE_UNKNOWN') void refresh() }

async function deploy() {
  const target = chosen.value
  if (!target) return
  // 密码只随这一次请求发出，发出后立即从页面上清掉
  const secret = password.value
  password.value = ''
  if (needsPassword.value && !secret) return
  warning.value = ''
  await act(async () => {
    const next = dataOf(await auth.api.POST('/api/v1/agents/instances/{instance_id}/deploy', { params: { header, path: path.value }, body: { server_id: target.id, version: props.instance.version, ...(secret ? { password: secret } : {}) } }))
    put(next); warning.value = next.capacity_warning ?? ''
  }, '已开始部署：下面显示每一步的进度')
  settle()
}
async function undeploy() {
  const files = removeFiles.value
  if (!window.confirm(files
    ? `从服务器上卸载「${props.instance.name}」并删除它的目录？实例凭证、OpenClaw 的会话与记忆都会删除，不可恢复。同一台服务器上的其他实例不受影响。`
    : `从服务器上卸载「${props.instance.name}」？会停止并删除它的容器、删掉它的定时任务，目录保留。同一台服务器上的其他实例不受影响。`)) return
  await act(async () => put(dataOf(await auth.api.POST('/api/v1/agents/instances/{instance_id}/undeploy', { params: { header, path: path.value }, body: { version: props.instance.version, remove_files: files } }))), '已开始卸载：下面显示每一步的进度')
  settle()
}
async function assign() {
  const serverId = assignTo.value === 'auto' ? null : assignTo.value
  await act(async () => put(dataOf(await auth.api.PUT('/api/v1/agents/instances/{instance_id}/server', { params: { header, path: path.value }, body: { server_id: serverId, version: props.instance.version } }))), serverId ? '已手动指定所在服务器' : '已改回按上报自动识别')
  settle()
}
</script>

<template>
  <section class="agent-section" aria-label="部署到服务器">
    <h3>部署到服务器</h3>
    <p v-if="state.isPending.value" role="status">正在加载…</p>
    <p v-else-if="state.error.value" role="alert">{{ sshError(state.error.value) }}</p>
    <template v-else>
      <dl class="agent-facts">
        <div>
          <dt>所在服务器</dt>
          <dd>
            <RouterLink v-if="current" :to="{ path: '/agents', query: { tab: 'servers', server: current.id } }">{{ current.name }}</RouterLink><template v-else>—</template>
            <template v-if="current"> · {{ deployment ? '经工作台部署' : instance.server_manual ? '手动指定' : SERVER_KIND.auto }}</template>
          </dd>
        </div>
        <div v-if="deployment?.install_dir"><dt>目录</dt><dd><code>{{ deployment.install_dir }}</code></dd></div>
        <div v-if="deployment"><dt>最近部署成功</dt><dd>{{ deployment.installed_at ? formatTime(deployment.installed_at) : '—' }}</dd></div>
      </dl>

      <SshJob v-if="job" :job="job" />

      <template v-if="!running">
        <p v-if="!supported" class="form-help">部署到服务器只支持外网模式的实例。内网模式请生成接入包，按部署教程在服务器上手动部署；部署后它会按上报自动归到服务器，也可以在下面手动指定。</p>
        <form v-else class="im-group-form" aria-label="部署" @submit.prevent="deploy">
          <p v-if="!deployment && !targets.length" class="form-help">
            还没有可以部署的服务器。先到 <RouterLink :to="{ path: '/agents', query: { tab: 'servers' } }">服务器</RouterLink> 页添加一台、核对主机公钥。
          </p>
          <template v-else>
            <label v-if="!deployment" class="form-field">
              <span>部署到</span>
              <select v-model="choice"><option v-for="s in targets" :key="s.id" :value="s.id">{{ s.name }}（{{ prepared(s) ? `已准备，${s.instances_total} 个实例` : '还没准备，要输一次密码' }}）</option></select>
            </label>
            <p class="form-help">
              {{ deployment
                ? '重新部署会重新生成接入包（旧凭证再保留 24 小时），更新这个实例目录里的文件并重启它；同一台服务器上的其他实例不受影响。'
                : '部署时用这台服务器的专用密钥上传接入包到这个实例自己的目录、安装 IM 插件、启动它自己的 Compose 项目、写它自己的定时任务，最后等它连上工作台。第一次要下载镜像，可能需要十几分钟。' }}
            </p>
            <template v-if="needsPassword">
              <label class="form-field"><span>登录密码 *</span><input v-model="password" type="password" autocomplete="off" maxlength="1024" /></label>
              <p class="form-help">这台服务器还没准备：先用这一次密码装上工作台的专用公钥、需要时安装 Docker，之后在这台服务器上部署不用再输密码。密码不保存，提交后输入框会清空。</p>
            </template>
            <p v-if="disabled" class="form-help">实例已停用，启用后才能部署。</p>
            <div class="form-actions">
              <button type="submit" class="primary-button" :disabled="busy || disabled || !chosen || (needsPassword && !password)">{{ deployment ? '重新部署' : '部署到这台服务器' }}</button>
            </div>
          </template>
        </form>

        <div v-if="deployment" class="im-group-form" role="group" aria-label="卸载">
          <label class="check-field"><input v-model="removeFiles" type="checkbox" /> 卸载时同时删除这个实例的目录（含实例凭证、OpenClaw 的会话与记忆，不可恢复）</label>
          <div class="form-actions"><button type="button" class="secondary-button im-danger" :disabled="busy" @click="undeploy">从服务器上卸载</button></div>
          <p class="form-help">卸载只停止并删除这个实例的容器和定时任务，不卸载 Docker、不吊销这台服务器的专用密钥，其他实例照常运行。</p>
        </div>

        <form v-else class="im-group-form" aria-label="手动指定所在服务器" @submit.prevent="assign">
          <label class="form-field">
            <span>手动接入的实例在哪台服务器</span>
            <select v-model="assignTo"><option value="auto">按上报自动识别</option><option v-for="s in all" :key="s.id" :value="s.id">{{ s.name }}</option></select>
          </label>
          <p class="form-help">手动部署的实例按上报的服务器指纹自动归到服务器；认错了可以在这里指定，以手动为准。</p>
          <div class="form-actions"><button type="submit" class="secondary-button" :disabled="busy || assignTo === (instance.server_manual && instance.server_id ? instance.server_id : 'auto')">保存所在服务器</button></div>
        </form>
      </template>

      <p v-if="warning" class="feedback" role="alert">{{ warning }}</p>
      <p v-if="error" role="alert">{{ error }}</p>
      <p v-if="notice" role="status">{{ notice }}</p>
    </template>
  </section>
</template>
