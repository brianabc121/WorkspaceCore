<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { PLATFORMS, TARGET_KIND, deployError, isSudoJobError, newDeployRequestId, type DeployTarget, type LlmBridgeApi, type Platform, type TargetKind } from './api'
import DeployJob from './DeployJob.vue'

// 页面上一键部署 MCP 管理器（docs/v2/LLM_BRIDGE_DEPLOY_PLAN.md 第 2、3、7 节）：先选服务器类型（智能体服务器 / 容器服务器），
// 再选服务器，工作台经 SSH 把管理器部署上去、连上它、登记好。只有 owner 能做。
// 和工作台同一台服务器时管理器加入工作台的内部网络；其他服务器上它只监听那台服务器的本机地址，工作台经专用的 SSH 通道访问。
// 登录密码（登录账号用 Docker 要 sudo 密码时才需要）只在提交时发出去，提交后输入框清空；页面不保存、不显示它。
// preselect：从服务器页面跳过来时（?deploy=<类型>:<服务器>）先选好那台服务器。
const props = defineProps<{ api: LlmBridgeApi; names: string[]; preselect?: { kind: TargetKind; id: string } | null }>()
const emit = defineEmits<{ deployed: [bridgeId: string, name: string]; cancel: [] }>()

const KINDS = Object.keys(TARGET_KIND) as TargetKind[]
// 一类服务器一台都没有时，写明去哪个页面添加
const ADD: Record<TargetKind, string> = {
  agent_server: '先到“智能体服务器”页添加一台服务器（填地址、账号、密码），核对指纹并初始化，然后回到这里。工作台自己所在的服务器也可以添加。',
  runner_host: '先到“容器服务器”页的“服务器设置”里添加一台，并用“通过 SSH 部署”部署一次，然后回到这里。',
}
const SOURCE: Record<TargetKind, string> = {
  agent_server: '列的是“智能体服务器”页里用账号密码添加的服务器。',
  runner_host: '列的是“容器服务器”页里登记的服务器；用 SSH 部署过的才能选。',
}
const targets = props.api.useDeployTargets()
const all = computed(() => targets.data.value?.targets ?? [])
const ALL = PLATFORMS.map(p => p.id)
const form = reactive({ kind: props.preselect?.kind ?? 'agent_server' as TargetKind, server: props.preselect?.id ?? '', name: '', platforms: [...ALL] as Platform[], password: '' })
const kind = computed(() => TARGET_KIND[form.kind])
const list = computed(() => all.value.filter(t => t.kind === form.kind))
const chosen = computed(() => list.value.find(s => s.id === form.server) ?? null)
const optionText = (s: DeployTarget) => [s.name, s.managers ? `已有 ${s.managers} 个 MCP 管理器` : '', s.deployable ? '' : `不能部署：${s.block_message ?? '暂不能部署'}`].filter(Boolean).join(' · ')

// 读到服务器之后：没有指定服务器、默认的那一类又一台都没有时，换到有服务器的那一类；
// 选中的服务器不在当前这一类里，就换成这一类里第一台能部署的
let settled = !!props.preselect
watch([all, () => form.kind], () => {
  if (!targets.data.value) return
  if (!settled) {
    settled = true
    const other = list.value.length ? undefined : KINDS.find(k => all.value.some(t => t.kind === k))
    if (other) { form.kind = other; return }
  }
  if (!list.value.some(s => s.id === form.server)) form.server = list.value.find(s => s.deployable)?.id ?? ''
}, { immediate: true })

const jobId = ref<string | null>(null)
const job = props.api.useDeployment(jobId)
const busy = ref(false), error = ref('')
// 哪台服务器刚才因为 sudo 密码没部署成（没填或不对）：回到表单，显示密码框和说明
const sudo = reactive({ server: '', hint: '' })
watch(() => form.server, () => { form.password = ''; sudo.hint = '' })
// 登录账号不是 root、也不在 docker 组的服务器，sudo 可能要密码：密码框可填可不填；已经知道要密码时必须填
const askPassword = computed(() => !!chosen.value && (chosen.value.may_need_password || sudo.server === chosen.value.id))
let requestId = newDeployRequestId()
const problem = computed(() => {
  const name = form.name.trim()
  if (!chosen.value) {
    if (!list.value.length) return `还没有${kind.value.name}。`
    return list.value.some(s => s.deployable) ? '选一台服务器。' : `这些服务器现在都不能部署（原因写在列表里）：先到“${kind.value.name}”页处理。`
  }
  if (!chosen.value.deployable) return `这台服务器现在不能部署：${chosen.value.block_message ?? '暂不能部署'}。`
  if (!name || name.length > 40) return '名字要 1–40 个字。'
  if (props.names.includes(name)) return '已经有同名的 MCP 管理器，换一个名字。'
  if (!form.platforms.length) return '至少勾选一个要控制的平台。'
  if (sudo.server === chosen.value.id && !form.password) return '填写这台服务器的登录密码。'
  return ''
})
async function submit() {
  if (busy.value) return
  if (problem.value) { error.value = problem.value; return }
  busy.value = true; error.value = ''
  const password = askPassword.value ? form.password : ''
  try {
    const started = await props.api.deploy({ target_kind: form.kind, target_id: form.server, name: form.name.trim(), platforms: ALL.filter(p => form.platforms.includes(p)), request_id: requestId, ...(password ? { sudo_password: password } : {}) })
    form.password = ''; sudo.hint = '' // 密码不留在页面上
    jobId.value = started.id
  } catch (e) { error.value = deployError(e) } finally { busy.value = false }
}
// 任务结束：成功就告诉上层（刷新管理器列表并选中它）；失败留在这里，可以改了再来。
// 因为 sudo 密码失败（没填或不对）：直接回到表单，填了密码再提交
watch(() => job.data.value?.status, status => {
  const data = job.data.value
  if (!data) return
  if (status === 'succeeded' && data.bridge_id) emit('deployed', data.bridge_id, data.name)
  else if (status === 'failed' && isSudoJobError(data.error_code)) {
    again()
    sudo.server = form.server
    sudo.hint = data.error_code === 'sudo_denied'
      ? '刚才没有部署成：登录密码不对，或者这个账号不能使用 sudo。重新填写登录密码后再部署。'
      : '刚才没有部署成：这台服务器的登录账号用 Docker 要 sudo 密码。在下面填写登录密码后再部署（只用这一次，不保存）。'
  }
})
// 换一个幂等号：用原来的再提交，拿回来的还是那个失败的任务
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
      <p class="form-help">工作台登录你选的服务器，把 MCP 管理器部署上去并登记好，不用到服务器上敲命令。和工作台同一台服务器时它加入工作台的内部网络；其他服务器上它只监听那台服务器的本机地址，工作台经专用的 SSH 通道访问，端口不对外开放。</p>
      <p v-if="targets.isPending.value" role="status" class="form-help">正在读取服务器…</p>
      <p v-else-if="targets.error.value" role="alert" class="lb-warn is-red">读不到服务器列表：{{ deployError(targets.error.value) }}</p>
      <template v-else>
        <fieldset class="lb-checks" aria-label="服务器类型">
          <legend>服务器类型</legend>
          <label v-for="k in KINDS" :key="k" class="check-field"><input v-model="form.kind" type="radio" name="lb-deploy-kind" :value="k" />{{ TARGET_KIND[k].name }}</label>
        </fieldset>
        <div v-if="!list.length" class="lb-empty">
          <p><strong>还没有{{ kind.name }}。</strong></p>
          <p>{{ ADD[form.kind] }}</p>
          <p><RouterLink class="asv-link" :to="kind.path">去{{ kind.name }}页</RouterLink></p>
        </div>
        <template v-else>
          <div class="lb-grid">
            <div class="form-field">
              <label for="lb-deploy-server">部署到哪台服务器</label>
              <select id="lb-deploy-server" v-model="form.server">
                <option value="" disabled>选一台服务器</option>
                <option v-for="s in list" :key="s.id" :value="s.id" :disabled="!s.deployable">{{ optionText(s) }}</option>
              </select>
              <p class="form-help">{{ SOURCE[form.kind] }}同一台服务器可以部署多个 MCP 管理器，互不相干。<RouterLink class="asv-link" :to="kind.path">管理服务器</RouterLink></p>
            </div>
            <div class="form-field"><label for="lb-deploy-name">名字</label><input id="lb-deploy-name" v-model="form.name" maxlength="40" required placeholder="例如：2 号" /><p class="form-help">智能体用这个名字指定走哪个 MCP 管理器。</p></div>
          </div>
          <p v-if="chosen && !chosen.deployable" role="note" class="lb-warn">这台服务器现在不能部署：{{ chosen.block_message ?? '暂不能部署' }}。<RouterLink class="asv-link" :to="kind.path">去{{ kind.name }}页处理</RouterLink></p>
          <fieldset class="lb-checks" aria-label="控制的平台">
            <legend>控制的平台</legend>
            <label v-for="p in PLATFORMS" :key="p.id" class="check-field"><input v-model="form.platforms" type="checkbox" :value="p.id" />{{ p.name }}</label>
          </fieldset>
          <p v-if="sudo.hint" role="alert" class="lb-warn">{{ sudo.hint }}</p>
          <div v-if="askPassword" class="form-field">
            <label for="lb-deploy-password">登录密码</label>
            <input id="lb-deploy-password" v-model="form.password" type="password" autocomplete="off" maxlength="256" />
            <p class="form-help">这台服务器的登录账号用 Docker 要 sudo 密码时填：只用这一次，不保存。免密 sudo 或 root 账号不用填。</p>
          </div>
          <p class="form-help">部署好之后在“平台与账号”里登录这些平台的账号。第一次部署要在服务器上构建镜像，通常要几分钟。</p>
        </template>
      </template>
      <p v-if="error" role="alert" class="lb-warn is-red">{{ error }}</p>
      <div class="lb-actions">
        <button type="button" class="secondary-button" :disabled="busy" @click="emit('cancel')">取消</button>
        <button v-if="list.length" type="submit" class="primary-button" :disabled="busy">{{ busy ? '正在开始…' : '开始部署' }}</button>
      </div>
    </form>
  </div>
</template>
