<script setup lang="ts">
import { computed, reactive, ref, toRef } from 'vue'
import { Plus } from '@lucide/vue'
import ManagementSection from '../../components/management/ManagementSection.vue'
import ActionFeedback from '../../components/ActionFeedback.vue'
import { PLATFORMS, SANDBOX, bridgeError, platformName, type Bridge, type BridgeStatus, type Environment, type EnvironmentBody, type LlmBridgeApi } from './api'

// 开发环境：智能体在哪个代码仓库、用什么权限干活。只有 Codex / Claude / WorkBuddy 有开发模式（ChatGPT 没有）；
// 用法固定是“开发”（云端通道目前只是占位）。
// 新增时只能选这个 MCP 管理器控制的平台（状态还没读到时不限制，由后端把关）。
const props = defineProps<{ api: LlmBridgeApi; bridge: Bridge; status?: BridgeStatus; canWrite: boolean }>()
const environments = props.api.useEnvironments(toRef(() => props.bridge.id))
const list = computed(() => environments.data.value ?? [])
const DEV_PLATFORMS = computed(() => PLATFORMS.filter(p => p.id !== 'chatgpt' && (!props.status?.reachable || props.status.platforms.includes(p.id))))
const SANDBOXES = Object.entries(SANDBOX) as [EnvironmentBody['sandbox'], string][]

const blank = () => ({ open: false, id: null as string | null, platform: (DEV_PLATFORMS.value[0]?.id ?? 'codex') as EnvironmentBody['platform'], name: '', repo_url: '', branch: '', sandbox: 'read_only' as EnvironmentBody['sandbox'], network: false, description: '', enabled: true })
const form = reactive(blank())
const error = ref(''), notice = ref(''), busy = ref(false)
const execWarning = computed(() => form.sandbox === 'exec' && (form.platform === 'claude' || form.platform === 'workbuddy'))

function openForm(env?: Environment) {
  Object.assign(form, blank(), { open: true }, env ? { id: env.id, platform: env.platform, name: env.name, repo_url: env.repo_url ?? '', branch: env.branch ?? '', sandbox: env.sandbox, network: env.network, description: env.description, enabled: env.enabled } : {})
  error.value = notice.value = ''
}
function closeForm() { Object.assign(form, blank()) }
async function run(action: () => Promise<string>) {
  if (busy.value) return
  busy.value = true; error.value = notice.value = ''
  try { notice.value = await action() } catch (e) { error.value = bridgeError(e) } finally { busy.value = false }
}
function submit() {
  const name = form.name.trim(), repo = form.repo_url.trim(), branch = form.branch.trim()
  if (!name) { error.value = '请填写环境的名字。'; return }
  if (repo && !/^https:\/\/\S+$/.test(repo)) { error.value = '代码仓库地址只支持 https:// 开头的地址。'; return }
  const body: EnvironmentBody = { platform: form.platform, mode: 'dev', name, repo_url: repo || null, branch: branch || null, sandbox: form.sandbox, network: form.network, description: form.description.trim(), enabled: form.enabled }
  const id = form.id
  return run(async () => { await props.api.saveEnvironment(props.bridge.id, id, body); closeForm(); return id ? `已保存环境「${name}」。` : `已新增环境「${name}」。` })
}
function remove(env: Environment) {
  if (!window.confirm(`删除开发环境「${env.name}」？已经用它建好的会话不受影响，只是不能再用它新建会话。`)) return
  return run(async () => { await props.api.deleteEnvironment(props.bridge.id, env.id); if (form.id === env.id) closeForm(); return `已删除环境「${env.name}」。` })
}
</script>

<template>
  <ManagementSection title="开发环境" :label="`开发环境：${bridge.name}`" description="开发环境决定智能体让大模型在哪个代码仓库、用什么权限干活。只有 Codex、Claude、WorkBuddy 有开发模式；用法固定是“开发”，云端通道目前只是占位。">
    <template v-if="canWrite && !form.open && DEV_PLATFORMS.length" #actions>
      <button type="button" class="primary-button" @click="openForm()"><Plus aria-hidden="true" />新增环境</button>
    </template>
    <p v-if="!DEV_PLATFORMS.length" role="note" class="lb-warn">这个 MCP 管理器没有控制带开发模式的平台（Codex / Claude / WorkBuddy），不能新增开发环境。</p>
    <ActionFeedback :error="error" :notice="notice" />
    <p v-if="environments.isPending.value" role="status" class="data-state">正在读取开发环境…</p>
    <div v-else-if="environments.error.value" class="data-state">
      <p role="alert">{{ bridgeError(environments.error.value) }}</p>
      <button type="button" class="secondary-button" @click="environments.refetch()">重试</button>
    </div>
    <p v-else-if="!list.length" class="ws-state-line">还没有开发环境。{{ canWrite ? '点“新增环境”添加一个。' : '' }}</p>
    <table v-else class="asv-table lb-table" aria-label="开发环境">
      <thead><tr><th scope="col">名字</th><th scope="col">平台</th><th scope="col">代码仓库</th><th scope="col">权限</th><th scope="col">状态</th><th v-if="canWrite" scope="col">操作</th></tr></thead>
      <tbody>
        <tr v-for="e in list" :key="e.id">
          <th scope="row">{{ e.name }}<span v-if="e.description" class="asv-sub">{{ e.description }}</span></th>
          <td data-label="平台">{{ platformName(e.platform) }}</td>
          <td data-label="代码仓库"><span class="asv-code">{{ e.repo_url || '没有仓库' }}</span><span v-if="e.branch" class="asv-sub">分支 {{ e.branch }}</span></td>
          <td data-label="权限">{{ SANDBOX[e.sandbox as keyof typeof SANDBOX] ?? e.sandbox }}<span class="asv-sub">{{ e.network ? '可以联网' : '不联网' }}</span></td>
          <td data-label="状态"><span class="asv-state" :class="e.enabled ? 'tone-green' : 'tone-muted'">{{ e.enabled ? '已启用' : '已停用' }}</span></td>
          <td v-if="canWrite" data-label="操作" class="lb-row-actions">
            <button type="button" class="asv-link" :disabled="busy" :aria-label="`修改环境${e.name}`" @click="openForm(e)">修改</button>
            <button type="button" class="asv-link lb-danger" :disabled="busy" :aria-label="`删除环境${e.name}`" @click="remove(e)">删除</button>
          </td>
        </tr>
      </tbody>
    </table>

    <form v-if="canWrite && form.open" class="asv-form lb-form" novalidate :aria-label="form.id ? '修改开发环境' : '新增开发环境'" @submit.prevent="submit">
      <h3>{{ form.id ? '修改开发环境' : '新增开发环境' }}</h3>
      <div class="lb-grid">
        <div class="form-field">
          <label for="lb-env-platform">平台</label>
          <select id="lb-env-platform" v-model="form.platform" :disabled="!!form.id"><option v-for="p in form.id ? PLATFORMS : DEV_PLATFORMS" :key="p.id" :value="p.id">{{ p.name }}</option></select>
          <p class="form-help">{{ form.id ? '建好后不能改平台。' : 'ChatGPT 没有开发模式。' }}</p>
        </div>
        <div class="form-field"><label for="lb-env-name">名字</label><input id="lb-env-name" v-model="form.name" maxlength="60" required /></div>
        <div class="form-field"><label for="lb-env-repo">代码仓库地址（可不填）</label><input id="lb-env-repo" v-model="form.repo_url" maxlength="500" inputmode="url" placeholder="https://github.com/owner/repo" /><p class="form-help">只支持 https:// 开头的地址。</p></div>
        <div class="form-field"><label for="lb-env-branch">分支（可不填）</label><input id="lb-env-branch" v-model="form.branch" maxlength="200" placeholder="main" /></div>
        <div class="form-field">
          <label for="lb-env-sandbox">权限</label>
          <select id="lb-env-sandbox" v-model="form.sandbox"><option v-for="[id, name] in SANDBOXES" :key="id" :value="id">{{ name }}</option></select>
        </div>
      </div>
      <p v-if="execWarning" role="note" class="lb-warn is-red">注意：{{ platformName(form.platform) }} 的命令以容器里的普通用户身份运行，没有操作系统级沙箱，能读到这个 MCP 管理器数据卷里的其他内容。</p>
      <div class="form-field"><label for="lb-env-desc">说明（可不填）</label><input id="lb-env-desc" v-model="form.description" maxlength="300" /></div>
      <label class="check-field"><input v-model="form.network" type="checkbox" />允许联网</label>
      <label class="check-field"><input v-model="form.enabled" type="checkbox" />启用这个环境</label>
      <div class="lb-actions">
        <button type="button" class="secondary-button" :disabled="busy" @click="closeForm">取消</button>
        <button type="submit" class="primary-button" :disabled="busy">{{ busy ? '正在保存…' : '保存' }}</button>
      </div>
    </form>
  </ManagementSection>
</template>
