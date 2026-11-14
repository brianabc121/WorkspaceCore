<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Plus } from '@lucide/vue'
import ManagementPage from '../../components/management/ManagementPage.vue'
import ManagementSection from '../../components/management/ManagementSection.vue'
import DataState from '../../components/DataState.vue'
import ActionFeedback from '../../components/ActionFeedback.vue'
import { isModuleDisabled } from '../../services/api'
import { PLATFORMS, bridgeError, deployError, isSudoJobError, useLlmBridge, type Bridge, type ManagedAction, type Platform, type TargetKind } from './api'
import ManagerCard from './ManagerCard.vue'
import ManagerOperation from './ManagerOperation.vue'
import DeployManager from './DeployManager.vue'
import DeployJob from './DeployJob.vue'
import BridgeAccounts from './BridgeAccounts.vue'
import BridgeSettingsForm from './BridgeSettingsForm.vue'
import BridgeEnvironments from './BridgeEnvironments.vue'
import BridgeSessions from './BridgeSessions.vue'
import BridgeGrants from './BridgeGrants.vue'
import './llm_bridge.css'

// 页面主体：MCP 管理器的列表（owner 增删改），选中一个后看它控制的平台与账号、设置、开发环境、会话；
// 最下面是按实例的权限。一个 MCP 管理器就是一座登记的会话桥：单独部署的容器，单独维护一套账号。
// 添加有两种方式：一键部署到服务器（工作台经 SSH 部署到智能体服务器或容器服务器并登记，docs/v2/LLM_BRIDGE_DEPLOY_PLAN.md），
// 或者手动添加已经部署好的。工作台部署的管理器可以重新部署、重启、卸载（后台任务，进度显示在列表下面）。
// 从服务器页面跳过来：?m=<管理器> 选中那个管理器；?deploy=<类型>:<服务器> 直接打开一键部署并选好那台服务器。
const props = defineProps<{ scope: string }>()
const route = useRoute(), router = useRouter()
const api = useLlmBridge(props.scope)
const canWrite = api.canWrite
const bridges = api.useBridges()
const list = computed(() => bridges.data.value ?? [])
const one = (value: unknown) => (typeof value === 'string' && value ? value : null)

const selectedId = ref<string | null>(null)
// 地址里指定的管理器只定位一次：之后点别的卡片、列表刷新都不会被它拉回去
let located: string | null = null
watch([list, () => one(route.query.m)], ([items, asked]) => {
  if (asked && asked !== located && items.some(b => b.id === asked)) { located = asked; selectedId.value = asked }
  else if (!items.some(b => b.id === selectedId.value)) selectedId.value = items[0]?.id ?? null
}, { immediate: true })
const selected = computed(() => list.value.find(b => b.id === selectedId.value) ?? null)
const status = api.useStatus(selectedId)

const TABS = [{ key: 'accounts', name: '平台与账号' }, { key: 'settings', name: '设置' }, { key: 'environments', name: '开发环境' }, { key: 'sessions', name: '会话' }] as const
const tab = ref<(typeof TABS)[number]['key']>('accounts')

const ALL = PLATFORMS.map(p => p.id)
const error = ref(''), notice = ref(''), busy = ref(false)
// managed：修改的是工作台部署的管理器——地址和令牌由部署管理，只能改名字
const form = reactive({ open: false, id: null as string | null, managed: false, name: '', base_url: '', token: '', platforms: [...ALL] as Platform[] })
// 一键部署：这个工作台带着管理器的构建文件时才有（接口里的 available）；只有 owner 能做
const deployments = api.useDeployments()
const canDeploy = computed(() => canWrite.value && !!deployments.data.value?.available)
const addMode = ref<'deploy' | 'manual'>('manual')
// 一键部署表单先选好哪台服务器（从服务器页面跳过来时）
const preselect = ref<{ kind: TargetKind; id: string } | null>(null)
// 工作台部署的管理器上正在确认的操作（重新部署 / 重启 / 卸载）；和添加、修改的表单同一时间只开一个
const operation = reactive({ bridge: null as Bridge | null, action: 'redeploy' as ManagedAction, askPassword: false, hint: '', round: 0 })
function openForm(bridge?: Bridge) {
  Object.assign(form, { open: true, id: bridge?.id ?? null, managed: !!bridge?.deployment, name: bridge?.name ?? '', base_url: bridge?.base_url ?? '', token: '', platforms: [...ALL] })
  addMode.value = !bridge && canDeploy.value ? 'deploy' : 'manual'
  operation.bridge = null
  error.value = notice.value = ''
}
function closeForm() { Object.assign(form, { open: false, id: null, managed: false, name: '', base_url: '', token: '', platforms: [...ALL] }); preselect.value = null }
// ?deploy=<类型>:<服务器>：能一键部署时打开表单并选好那台服务器，然后把这个参数从地址里去掉（刷新、后退不会再打开一次）
watch([() => one(route.query.deploy), canDeploy], ([asked, can]) => {
  const found = /^(agent_server|runner_host):([\w-]{1,64})$/.exec(asked ?? '')
  if (!found || !can) return
  openForm()
  preselect.value = { kind: found[1] as TargetKind, id: found[2]! }
  const query = { ...route.query }
  delete query.deploy
  void router.replace({ query })
}, { immediate: true })
const formProblem = computed(() => {
  const name = form.name.trim(), url = form.base_url.trim()
  if (!name || name.length > 40) return '名字要 1–40 个字。'
  if (form.managed) return ''
  if (!/^https?:\/\/\S+$/.test(url) || url.length > 300) return '地址要以 http:// 或 https:// 开头，例如 http://llm-bridge:8787。'
  if (form.token ? form.token.length < 32 || form.token.length > 200 : !form.id) return '令牌至少 32 个字符（部署这个 MCP 管理器时生成的管理令牌）。'
  if (!form.id && !form.platforms.length) return '至少勾选一个要控制的平台。'
  return ''
})
async function run(action: () => Promise<string>) {
  if (busy.value) return
  busy.value = true; error.value = notice.value = ''
  try { notice.value = await action() } catch (e) { error.value = bridgeError(e) } finally { busy.value = false }
}
function submit() {
  if (formProblem.value) { error.value = formProblem.value; return }
  const name = form.name.trim(), base_url = form.base_url.trim(), token = form.token, id = form.id, managed = form.managed
  // 按固定顺序提交，和页面上的顺序一致
  const platforms = ALL.filter(p => form.platforms.includes(p))
  return run(async () => {
    if (id) await api.updateBridge(id, managed ? { name } : { name, base_url, ...(token ? { token } : {}) })
    else selectedId.value = (await api.createBridge({ name, base_url, token, platforms })).id
    closeForm() // 令牌不留在页面上
    if (!id) tab.value = 'accounts'
    return id ? `已保存「${name}」。` : `已添加「${name}」。接下来在下面的“平台与账号”里登录它控制的平台。`
  })
}
function toggle(bridge: Bridge) {
  if (bridge.enabled && !window.confirm(`停用「${bridge.name}」？停用后智能体不能再经它发指令，它的登录状态和会话记录都保留。`)) return
  return run(async () => { await api.updateBridge(bridge.id, { enabled: !bridge.enabled }); return bridge.enabled ? `已停用「${bridge.name}」。` : `已启用「${bridge.name}」。` })
}
async function deployed(id: string, name: string) {
  await Promise.all([api.invalidate('bridges'), api.invalidate('deployments'), api.invalidate('deploy-targets'), api.invalidate('installs')])
  selectedId.value = id; tab.value = 'accounts'
  notice.value = `已部署并添加「${name}」。接下来在下面的“平台与账号”里登录它控制的平台。`
}

// 工作台部署的管理器：重新部署（升级）、重启、卸载都是后台任务。先在确认框里说明（需要时填登录密码），
// 开始后进度显示在列表下面
const jobId = ref<string | null>(null)
const job = api.useDeployment(jobId)
// 进度里的任务是哪个管理器上的哪种操作：因为 sudo 密码失败时把同一个确认框重新打开
let running: { job: string; bridge: string; action: ManagedAction } | null = null
function operate(bridge: Bridge, action: ManagedAction, sudo?: { hint: string }) {
  Object.assign(operation, { bridge, action, askPassword: !!sudo, hint: sudo?.hint ?? '', round: operation.round + 1 })
  closeForm()
  error.value = notice.value = ''
}
function operationStarted(id: string) {
  if (operation.bridge) running = { job: id, bridge: operation.bridge.id, action: operation.action }
  operation.bridge = null
  jobId.value = id
}
// 按“哪个任务 + 状态”看：接连两个任务都已经结束时（例如只删除登记，返回时就是成功）也要各刷新一次
watch(() => (job.data.value ? `${job.data.value.id}:${job.data.value.status}` : ''), async () => {
  const data = job.data.value, status = data?.status
  if (!data || status === 'running') return
  await Promise.all([api.invalidate('bridges'), api.invalidate('grants'), api.invalidate('deployments'), api.invalidate('deploy-targets'), api.invalidate('installs')])
  // 登录密码没填或不对（任务在“检查”一步失败）：重新打开确认框，填了密码再来
  if (status !== 'failed' || !isSudoJobError(data.error_code) || running?.job !== data.id) return
  const bridge = list.value.find(b => b.id === running?.bridge)
  if (bridge && !operation.bridge) {
    operate(bridge, running.action, { hint: data.error_code === 'sudo_denied'
      ? '刚才没有成功：登录密码不对，或者这个账号不能使用 sudo。重新填写登录密码后再试。'
      : '刚才没有成功：这台服务器的登录账号用 Docker 要 sudo 密码。填写登录密码后再试（只用这一次，不保存）。' })
  }
})

function remove(bridge: Bridge) {
  if (!window.confirm(`删除「${bridge.name}」？\n\n只是从工作台里去掉它，不影响它的容器和里面的登录、会话。只允许用它的实例，之后一个 MCP 管理器都用不了，要到“实例权限”里重新选。`)) return
  return run(async () => { await api.deleteBridge(bridge.id); await api.invalidate('grants'); if (form.id === bridge.id) closeForm(); return `已删除「${bridge.name}」。` })
}
</script>

<template>
  <ManagementPage title="大模型客户端管理" description="添加一个或多个 MCP 管理器。每个管理器控制你勾选的平台（ChatGPT / Codex / Claude / WorkBuddy），单独维护一套账号；智能体经它向这些平台发指令、管理会话。">
    <p v-if="isModuleDisabled(bridges.error.value)" role="status">功能已停用：大模型客户端在当前工作空间没有启用。</p>
    <DataState v-else-if="bridges.isPending.value || bridges.error.value" :loading="bridges.isPending.value" :error="bridges.error.value" :empty="false" empty-text="" @retry="bridges.refetch()" />
    <template v-else>
      <p v-if="!canWrite" role="note" class="mg-hint">只有 owner 能修改大模型客户端；你看到的是只读视图。</p>

      <ManagementSection title="MCP 管理器" description="一个 MCP 管理器在每个平台上只登录一个账号。同一个平台要用第二个账号，就再添加一个 MCP 管理器。">
        <template v-if="canWrite && !form.open" #actions>
          <button type="button" class="primary-button" @click="openForm()"><Plus aria-hidden="true" />添加 MCP 管理器</button>
        </template>
        <ActionFeedback :error="error" :notice="notice" />

        <div v-if="!list.length" class="lb-empty">
          <p><strong>还没有 MCP 管理器。</strong></p>
          <p>MCP 管理器是一个单独部署的容器，里面装着各平台的官方程序；账号的登录保存在它里面，不保存在工作台。</p>
          <p>每个管理器控制你勾选的平台，在每个平台上只登录一个账号；可以添加多个，各自一套账号。</p>
          <p>{{ canDeploy ? '点“添加 MCP 管理器”：可以一键部署到智能体服务器或容器服务器，也可以手动添加已经部署好的。' : canWrite ? '点“添加 MCP 管理器”，按里面的步骤部署并填写地址和令牌。' : '请 owner 来添加。' }}</p>
        </div>
        <div v-else class="lb-managers" role="list" aria-label="MCP 管理器列表">
          <div v-for="b in list" :key="b.id" role="listitem">
            <ManagerCard :api="api" :bridge="b" :selected="b.id === selectedId" :can-write="canWrite" :busy="busy" @select="selectedId = b.id" @edit="openForm(b)" @toggle="toggle(b)" @remove="remove(b)" @operate="operate(b, $event)" />
          </div>
        </div>

        <ManagerOperation v-if="canWrite && operation.bridge" :key="`${operation.bridge.id}:${operation.action}:${operation.round}`" :api="api" :bridge="operation.bridge" :action="operation.action" :ask-password="operation.askPassword" :hint="operation.hint" @started="operationStarted" @cancel="operation.bridge = null" />

        <div v-if="jobId" class="lb-form">
          <p v-if="job.isPending.value" role="status" class="form-help">正在读取进度…</p>
          <DeployJob v-else-if="job.data.value" :job="job.data.value" />
          <p v-if="job.error.value" role="alert" class="lb-warn is-red">读不到进度：{{ deployError(job.error.value) }}</p>
          <div v-if="!job.data.value || job.data.value.status !== 'running'" class="lb-actions"><button type="button" class="secondary-button" @click="jobId = null">关闭</button></div>
        </div>

        <div v-if="canWrite && form.open && !form.id && canDeploy" class="lb-form lb-add">
          <h3>添加 MCP 管理器</h3>
          <div class="lb-modes" role="group" aria-label="添加方式">
            <button type="button" :aria-pressed="addMode === 'deploy'" @click="addMode = 'deploy'">一键部署到服务器</button>
            <button type="button" :aria-pressed="addMode === 'manual'" @click="addMode = 'manual'">手动添加（已经部署好了）</button>
          </div>
          <DeployManager v-if="addMode === 'deploy'" :key="preselect ? `${preselect.kind}:${preselect.id}` : 'any'" :api="api" :names="list.map(b => b.name)" :preselect="preselect" @deployed="deployed" @cancel="closeForm" />
        </div>

        <form v-if="canWrite && form.open && (form.id || addMode === 'manual')" class="asv-form lb-form" novalidate :aria-label="form.managed ? '给 MCP 管理器改名' : form.id ? '修改 MCP 管理器' : '添加 MCP 管理器'" @submit.prevent="submit">
          <h3>{{ form.managed ? '给 MCP 管理器改名' : form.id ? '修改 MCP 管理器' : canDeploy ? '手动添加' : '添加 MCP 管理器' }}</h3>
          <details v-if="!form.id" class="lb-deploy">
            <summary>还没有部署？先在服务器上启动它</summary>
            <ol class="lb-steps">
              <li>第一个管理器：在工作台的服务器上运行 <span class="asv-code">make llm-bridge-up</span>。地址填 <span class="asv-code">http://llm-bridge:8787</span>，令牌用 <span class="asv-code">make llm-bridge-token</span> 查看。</li>
              <li>第 2 个、第 3 个……：运行 <span class="asv-code">make llm-bridge-up N=2</span>。地址填 <span class="asv-code">http://llm-bridge-2:8787</span>，令牌用 <span class="asv-code">make llm-bridge-token N=2</span> 查看（N 换成 3、4 同理）。</li>
              <li>部署在别的服务器上时，见部署指南的“大模型客户端”一节。</li>
            </ol>
          </details>
          <div class="lb-grid">
            <div class="form-field"><label for="lb-bridge-name">名字</label><input id="lb-bridge-name" v-model="form.name" maxlength="40" required placeholder="例如：1 号" /><p class="form-help">智能体用这个名字指定走哪个 MCP 管理器。</p></div>
            <div v-if="!form.managed" class="form-field"><label for="lb-bridge-url">地址</label><input id="lb-bridge-url" v-model="form.base_url" maxlength="300" required inputmode="url" placeholder="http://llm-bridge:8787" /><p class="form-help">工作台访问这个管理器的地址。</p></div>
          </div>
          <p v-if="form.managed" class="form-help">这个管理器是工作台部署的：地址和令牌由部署管理，这里只能改名字。</p>
          <div v-else class="form-field">
            <label for="lb-bridge-token">管理令牌</label>
            <input id="lb-bridge-token" v-model="form.token" type="password" autocomplete="new-password" maxlength="200" :required="!form.id" :placeholder="form.id ? '已保存（不回显）；不填表示不改' : '部署时生成的管理令牌，至少 32 个字符'" />
            <p class="form-help">令牌加密保存，页面上不再显示。</p>
          </div>
          <fieldset v-if="!form.id" class="lb-checks" aria-label="控制的平台">
            <legend>控制的平台</legend>
            <label v-for="p in PLATFORMS" :key="p.id" class="check-field"><input v-model="form.platforms" type="checkbox" :value="p.id" />{{ p.name }}</label>
          </fieldset>
          <p v-if="!form.id" class="form-help">只有勾选的平台能在这个管理器上登录账号、被智能体使用；之后可以在“平台与账号”里改。ChatGPT 和 Codex 用的是同一个 ChatGPT 账号。</p>
          <div class="lb-actions">
            <button type="button" class="secondary-button" :disabled="busy" @click="closeForm">取消</button>
            <button type="submit" class="primary-button" :disabled="busy">{{ busy ? (form.id ? '正在保存…' : '正在连接…') : form.id ? '保存' : '添加' }}</button>
          </div>
        </form>
      </ManagementSection>

      <section v-if="selected" class="lb-bridge" :aria-label="`MCP 管理器：${selected.name}`">
        <div class="lb-tabs" role="tablist" :aria-label="`MCP 管理器「${selected.name}」`">
          <button v-for="t in TABS" :key="t.key" type="button" role="tab" :aria-selected="tab === t.key" @click="tab = t.key">{{ t.name }}</button>
        </div>
        <div role="tabpanel" :aria-label="TABS.find(t => t.key === tab)?.name" class="lb-panel">
          <BridgeAccounts v-if="tab === 'accounts'" :key="selected.id" :api="api" :bridge="selected" :status="status.data.value" :loading="status.isPending.value" :load-error="status.error.value" :can-write="canWrite" @reload="status.refetch()" />
          <BridgeSettingsForm v-else-if="tab === 'settings'" :key="`s:${selected.id}`" :api="api" :bridge="selected" :status="status.data.value" :loading="status.isPending.value" :load-error="status.error.value" :can-write="canWrite" @reload="status.refetch()" />
          <BridgeEnvironments v-else-if="tab === 'environments'" :key="`e:${selected.id}`" :api="api" :bridge="selected" :status="status.data.value" :can-write="canWrite" />
          <BridgeSessions v-else :key="`c:${selected.id}`" :api="api" :bridge="selected" />
        </div>
      </section>

      <BridgeGrants :api="api" :bridges="list" :can-write="canWrite" />
    </template>
  </ManagementPage>
</template>
